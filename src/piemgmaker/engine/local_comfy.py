"""LocalComfy 어댑터 — PM_BACKEND_URL의 ComfyUI에 접속한다.

서버가 미기동이고 온디맨드 레이어(comfy_up.sh)가 감지되면 기동하고,
run/last_active 하트비트로 idle-watchdog가 생성 중 서버를 내리지 않게 한다.
"""

import copy
import json
import logging
import os
import subprocess
import time
from pathlib import Path

import httpx

from piemgmaker.config import Config
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

ENV_ONDEMAND_DIR = "PM_COMFY_ONDEMAND_DIR"
DEFAULT_ONDEMAND_DIR = Path.home() / "comfy-ondemand"


class LocalComfyEngine:
    name = "local-comfy"

    def __init__(
        self,
        config: Config,
        client: httpx.Client | None = None,
        ondemand_dir: Path | None = None,
    ):
        if client is None:
            headers = {}
            if config.backend_auth:
                headers["Authorization"] = f"Bearer {config.backend_auth}"
            # read를 길게: 대형 모델 로드가 서버 이벤트 루프를 막아도 폴링이 끊기지 않게
            client = httpx.Client(
                base_url=config.backend_url,
                headers=headers,
                timeout=httpx.Timeout(connect=10, read=180, write=30, pool=30),
            )
        self._client = client
        env_dir = os.environ.get(ENV_ONDEMAND_DIR)
        self._ondemand_dir = ondemand_dir or (Path(env_dir) if env_dir else DEFAULT_ONDEMAND_DIR)

    def _touch_active(self) -> None:
        # 온디맨드 레이어가 없으면(외부 서버 접속) 하트비트를 남기지 않는다
        if not self._ondemand_dir.exists():
            return
        run_dir = self._ondemand_dir / "run"
        try:
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "last_active").write_text(str(int(time.time())))
        except OSError as exc:
            # 하트비트 실패는 잡을 죽일 사유가 아니다 — 경고만 남긴다
            log.warning("idle-watchdog 하트비트 갱신 실패: %s", exc)

    def _is_up(self) -> bool:
        try:
            return self._client.get("/system_stats").status_code == 200
        except httpx.TransportError:
            return False

    def ensure_up(self, timeout_s: int = 180) -> None:
        if self._is_up():
            return
        up_script = self._ondemand_dir / "comfy_up.sh"
        if not up_script.is_file():
            raise EngineError(f"ComfyUI 미기동이고 기동 스크립트가 없습니다: {up_script}")
        subprocess.run(["/bin/bash", str(up_script)], check=True, capture_output=True)
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self._is_up():
                return
            time.sleep(2)
        raise EngineError(f"ComfyUI 기동 대기 초과({timeout_s}s)")

    def _upload_image(self, path: Path) -> str:
        with path.open("rb") as f:
            resp = self._client.post(
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
        self.ensure_up()
        self._touch_active()
        graph = copy.deepcopy(payload.graph)
        if payload.input_images:
            refs = {
                slot: self._upload_image(Path(local_path))
                for slot, local_path in payload.input_images.items()
            }
            graph = fill_slots(graph, refs)
        remote_ids: list[str] = []
        for seed in payload.seeds:
            seeded = inject_seed(copy.deepcopy(graph), seed)
            resp = self._client.post(
                "/prompt", json={"prompt": seeded, "client_id": payload.job_id}
            )
            if resp.status_code != 200:
                raise JobFailedError(f"/prompt 거부({resp.status_code}): {resp.text[:1000]}")
            remote_ids.append(resp.json()["prompt_id"])
        return JobHandle(
            engine=self.name,
            job_id=payload.job_id,
            seeds=list(payload.seeds),
            remote_ids=remote_ids,
            output_index=payload.output_index,
        )

    def poll(self, handle: JobHandle) -> JobStatus:
        self._touch_active()
        total = len(handle.remote_ids)
        done = 0
        for remote_id in handle.remote_ids:
            try:
                entry = self._history_entry(remote_id, required=False)
            except httpx.TransportError as exc:
                # 모델 로드 중 서버 무응답·순단은 일시 장애 — 잡 실패로 승격하지 않는다
                log.warning("폴링 일시 장애(계속 진행): %s", exc)
                return JobStatus(state="running", done_count=done, total=total)
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
        dest_dir.mkdir(parents=True, exist_ok=True)
        results: list[CandidateResult] = []
        for index, (remote_id, seed) in enumerate(zip(handle.remote_ids, handle.seeds)):
            entry = self._history_entry(remote_id, required=True)
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
            resp = self._client.get(
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
        self._touch_active()
        return results

    def cancel(self, handle: JobHandle) -> None:
        """베스트 에포트 취소 — 이 잡의 대기 프롬프트만 삭제하고,
        현재 실행 중인 것이 이 잡일 때만 인터럽트한다 (전역 인터럽트는 남의 잡을 죽인다)."""
        try:
            self._client.post("/queue", json={"delete": handle.remote_ids})
            resp = self._client.get("/queue")
            if resp.status_code == 200:
                running = resp.json().get("queue_running", [])
                running_ids = {entry[1] for entry in running if len(entry) > 1}
                if running_ids & set(handle.remote_ids):
                    self._client.post("/interrupt")
        except httpx.TransportError as exc:
            log.warning("엔진 취소 요청 실패(무시): %s", exc)

    def _history_entry(self, remote_id: str, required: bool) -> dict | None:
        resp = self._client.get(f"/history/{remote_id}")
        if resp.status_code != 200:
            if required:
                raise JobFailedError(f"/history 조회 실패({resp.status_code}): {remote_id}")
            log.warning("/history 조회 실패(%s): %s", resp.status_code, remote_id)
            return None
        entry = resp.json().get(remote_id)
        if required and (not entry or not entry.get("outputs")):
            raise JobFailedError(f"완료되지 않은 잡의 결과 회수 시도: {remote_id}")
        return entry
