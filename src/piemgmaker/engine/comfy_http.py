"""ComfyUI HTTP 코어 — 로컬·원격 어댑터가 공유하는 submit/poll/fetch/cancel.

입력은 /upload/image 업로드, 출력은 /view 다운로드라 공유 파일시스템 전제가 없다.
서브클래스는 _client_for(group)로 요청을 보낼 httpx.Client를 공급하고, 기동 방식
(로컬 subprocess / 원격 콜드 스타트 대기)은 ensure_up에서 각자 구현한다.
"""

import copy
import json
import logging
from pathlib import Path

import httpx

from piemgmaker.engine.contract import (
    CandidateResult,
    EngineError,
    JobFailedError,
    JobHandle,
    JobStatus,
)
from piemgmaker.schemas.generation import JobPayload
from piemgmaker.workflows.render import fill_slots, inject_seed

log = logging.getLogger(__name__)

# 연속 전송 실패가 이 횟수에 닿으면 서버 생사를 재확인한다 — 원격 장애 시
# "진행 중"으로 무한정 붙들려 단일 워커 큐가 몇 시간 멈추는 것을 막는다
POLL_FAILURE_LIMIT = 5


class ComfyHTTPEngine:
    name = "comfy-http"
    # 상태 프로브가 유휴 인스턴스를 깨울 수 있는가 (원격 scale-to-zero 백엔드만 True)
    probe_may_wake = False

    def __init__(self) -> None:
        self._poll_failures = 0

    # ── 서브클래스 훅 ──

    def _client_for(self, group: str | None = None) -> httpx.Client:
        raise NotImplementedError

    def _on_activity(self) -> None:
        """잡 진행 신호 — 로컬 idle-watchdog 하트비트용. 원격은 필요 없다."""

    def ensure_up(self, group: str | None = None) -> None:
        raise NotImplementedError

    # ── HTTP 코어 ──

    def _is_up(self, group: str | None = None) -> bool:
        try:
            return self._client_for(group).get("/system_stats").status_code == 200
        except httpx.TransportError:
            return False

    def _upload_image(self, path: Path, group: str | None = None) -> str:
        with path.open("rb") as f:
            resp = self._client_for(group).post(
                "/upload/image",
                files={"image": (path.name, f, "image/png")},
                data={"overwrite": "true"},
            )
        if resp.status_code != 200:
            raise JobFailedError(f"/upload/image 실패({resp.status_code}): {resp.text[:500]}")
        info = resp.json()
        sub = info.get("subfolder") or ""
        return f"{sub}/{info['name']}" if sub else info["name"]

    def submit(self, payload: JobPayload) -> JobHandle:
        group = payload.backend_group
        self.ensure_up(group)
        self._on_activity()
        client = self._client_for(group)
        graph = copy.deepcopy(payload.graph)
        if payload.input_images:
            refs = {
                slot: self._upload_image(Path(local_path), group)
                for slot, local_path in payload.input_images.items()
            }
            graph = fill_slots(graph, refs)
        remote_ids: list[str] = []
        for seed in payload.seeds:
            seeded = inject_seed(copy.deepcopy(graph), seed)
            resp = client.post("/prompt", json={"prompt": seeded, "client_id": payload.job_id})
            if resp.status_code != 200:
                raise JobFailedError(f"/prompt 거부({resp.status_code}): {resp.text[:1000]}")
            remote_ids.append(resp.json()["prompt_id"])
        return JobHandle(
            engine=self.name,
            job_id=payload.job_id,
            seeds=list(payload.seeds),
            remote_ids=remote_ids,
            output_index=payload.output_index,
            engine_group=group,
        )

    def poll(self, handle: JobHandle) -> JobStatus:
        self._on_activity()
        group = handle.engine_group
        total = len(handle.remote_ids)
        done = 0
        for remote_id in handle.remote_ids:
            try:
                entry = self._history_entry(remote_id, required=False, group=group)
            except httpx.TransportError as exc:
                # 모델 로드 중 서버 무응답·순단은 일시 장애 — 잡 실패로 승격하지 않는다.
                # 다만 연속 실패가 쌓이면 서버 생사를 확인하고 죽었으면 즉시 올린다
                self._poll_failures += 1
                if self._poll_failures >= POLL_FAILURE_LIMIT and not self._is_up(group):
                    raise EngineError(
                        f"엔진 응답 없음 — 연속 폴링 실패 {self._poll_failures}회: {exc}"
                    ) from exc
                log.warning("폴링 일시 장애(계속 진행): %s", exc)
                return JobStatus(state="running", done_count=done, total=total)
            self._poll_failures = 0
            if entry is None:
                continue
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                messages = json.dumps(status.get("messages", []), ensure_ascii=False)
                return JobStatus(
                    state="failed", done_count=done, total=total, error=messages[:2000]
                )
            if entry.get("outputs"):
                done += 1
        state = "done" if done == total else ("running" if done else "queued")
        return JobStatus(state=state, done_count=done, total=total)

    def fetch(self, handle: JobHandle, dest_dir: Path) -> list[CandidateResult]:
        group = handle.engine_group
        client = self._client_for(group)
        dest_dir.mkdir(parents=True, exist_ok=True)
        results: list[CandidateResult] = []
        for index, (remote_id, seed) in enumerate(zip(handle.remote_ids, handle.seeds)):
            entry = self._history_entry(remote_id, required=True, group=group)
            images = [
                img
                for node_out in entry.get("outputs", {}).values()
                for img in node_out.get("images", []) or []
                if img.get("type") != "temp"
            ]
            if not images:
                raise JobFailedError(f"출력 이미지 없음: remote_id={remote_id}")
            try:
                img = images[handle.output_index]
            except IndexError:
                raise JobFailedError(
                    f"출력 인덱스 {handle.output_index} 없음 (출력 {len(images)}장): {remote_id}"
                ) from None
            resp = client.get(
                "/view",
                params={
                    "filename": img["filename"],
                    "subfolder": img.get("subfolder", ""),
                    "type": img.get("type", "output"),
                },
            )
            resp.raise_for_status()
            path = dest_dir / f"{index:02d}_{seed}.png"
            path.write_bytes(resp.content)
            results.append(CandidateResult(index=index, seed=seed, path=path))
        self._on_activity()
        return results

    def cancel(self, handle: JobHandle) -> None:
        """베스트 에포트 취소 — 이 잡의 대기 프롬프트만 삭제하고,
        현재 실행 중인 것이 이 잡일 때만 인터럽트한다 (전역 인터럽트는 남의 잡을 죽인다)."""
        client = self._client_for(handle.engine_group)
        try:
            client.post("/queue", json={"delete": handle.remote_ids})
            resp = client.get("/queue")
            if resp.status_code == 200:
                running = resp.json().get("queue_running", [])
                running_ids = {entry[1] for entry in running if len(entry) > 1}
                if running_ids & set(handle.remote_ids):
                    client.post("/interrupt")
        except httpx.TransportError as exc:
            log.warning("엔진 취소 요청 실패(무시): %s", exc)

    def queue_snapshot(self, group: str | None = None) -> dict:
        """엔진 큐 상태 — ensure_up을 호출하지 않는다(유휴 인스턴스를 깨우지 않기 위함).

        entries는 큐 엔트리 원본이라 호출자가 자기 잡과 외부 잡을 분류할 수 있다.
        """
        try:
            resp = self._client_for(group).get("/queue")
        except httpx.HTTPError as exc:
            log.warning("엔진 큐 조회 실패: %s", exc)
            return _unreachable_queue()
        if resp.status_code != 200:
            log.warning("엔진 큐 조회 실패(%s)", resp.status_code)
            return _unreachable_queue()
        try:
            data = resp.json()
        except ValueError as exc:
            # 프록시 오류 페이지 등 비-JSON 200 응답 — 상태 패널이 500을 내는 대신 도달 불가로 흡수한다
            log.warning("엔진 큐 응답 파싱 실패: %s", exc)
            return _unreachable_queue()
        running = list(data.get("queue_running", []))
        pending = list(data.get("queue_pending", []))
        return {
            "reachable": True,
            "state": "up",
            "busy": bool(running or pending),
            "running": len(running),
            "pending": len(pending),
            "entries": running + pending,
        }

    def _history_entry(
        self, remote_id: str, required: bool, group: str | None = None
    ) -> dict | None:
        resp = self._client_for(group).get(f"/history/{remote_id}")
        if resp.status_code != 200:
            if required:
                raise JobFailedError(f"/history 조회 실패({resp.status_code}): {remote_id}")
            log.warning("/history 조회 실패(%s): %s", resp.status_code, remote_id)
            return None
        entry = resp.json().get(remote_id)
        if required and (not entry or not entry.get("outputs")):
            raise JobFailedError(f"완료되지 않은 잡의 결과 회수 시도: {remote_id}")
        return entry


def _unreachable_queue() -> dict:
    return {
        "reachable": False,
        "state": "down",
        "busy": False,
        "running": 0,
        "pending": 0,
        "entries": [],
    }
