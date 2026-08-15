"""파일 기반 잡 스토어 + 단일 워커 큐.

로컬 GPU 1개 전제로 동시 실행 1. 상태의 단일 소스는 잡 디렉토리의 status.json.
서버 재시작 시 미완 잡(queued/preparing/running/postprocess)은 failed 처리한다.
"""

import json
import logging
import queue
import re
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import yaml

from piemgmaker.assets_lib.library import AssetLibrary
from piemgmaker.config import Config
from piemgmaker.engine import create_engine
from piemgmaker.engine.contract import WorkflowEngine
from piemgmaker.engine.contract import JobCancelledError
from piemgmaker.pipeline.orchestrate import build_job, execute_job
from piemgmaker.schemas.brief import BriefInput
from piemgmaker.schemas.model_profile import ModelProfileRegistry
from piemgmaker.schemas.style_pack import StylePack

log = logging.getLogger(__name__)

PENDING_STATES = {"queued", "preparing", "running", "postprocess"}
IMAGE_KINDS = ("candidates", "final", "inputs")


def _seed_out(seed: object) -> object:
    """시드를 웹으로 내보낼 때 문자열로 바꾼다.

    시드는 64비트 정수라 JS Number의 안전 범위(2^53-1)를 넘는다. 숫자로 내보내면
    브라우저가 JSON을 파싱하는 순간 하위 자리가 뭉개져, 서로 다른 후보 시드가
    화면에서 같은 값으로 보인다. 재현용 값이라 한 자리도 틀리면 안 된다.
    "random" 같은 문자열 리터럴과 None은 그대로 둔다.
    """
    return str(seed) if isinstance(seed, int) and not isinstance(seed, bool) else seed
ENGINE_STATUS_TTL_S = 3.0
# 엔진 상태 기본형 — state는 up(도달) / down(도달 불가) / idle(원격 유휴, 프로브 생략) /
# unknown(큐를 보고하지 않는 엔진). 기존 키는 웹 계약 유지를 위해 그대로 둔다
UNREACHABLE_ENGINE_STATUS = {
    "reachable": False,
    "busy": False,
    "running": 0,
    "pending": 0,
    "external": 0,
    "state": "down",
}
# submit 시 uuid4().hex[:12]로 생성 — 경로 결합 전 형식을 강제해 잡 디렉토리 밖으로의 탈출을 막는다
JOB_ID_RE = re.compile(r"^[0-9a-f]{12}$")


class JobNotFound(KeyError):
    pass


class JobStore:
    def __init__(
        self,
        config: Config,
        packs: dict[str, StylePack],
        assets_dir: Path,
        engine_factory: Callable[[], WorkflowEngine] | None = None,
        timeout_s: float = 14400.0,
        profiles: ModelProfileRegistry | None = None,
    ):
        self.config = config
        self.packs = packs
        self.assets_dir = assets_dir
        self.profiles = profiles
        self.jobs_root = config.storage / "jobs"
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self._engine_factory = engine_factory or (lambda: create_engine(config))
        self._timeout_s = timeout_s
        self._queue: queue.Queue[str] = queue.Queue()
        # 상태 프로브용 엔진은 재사용한다 — 폴링마다 새 HTTP 클라이언트를 만들지 않게
        self._status_engine: WorkflowEngine | None = None
        self._engine_cache: tuple[float, dict] | None = None
        self._fail_interrupted()

    def start_worker(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name="pm-job-worker").start()

    def _loop(self) -> None:
        while True:
            job_id = self._queue.get()
            try:
                self.process(job_id)
            except Exception:
                # 워커 스레드가 죽으면 큐 전체가 멈춘다 — 기록 후 다음 잡으로
                log.exception("잡 실행 중 미처리 예외: %s", job_id)

    def _fail_interrupted(self) -> None:
        for job_dir in self.jobs_root.iterdir():
            if not job_dir.is_dir():
                continue
            status = self._read_json(job_dir / "status.json")
            if status and status.get("state") in PENDING_STATES:
                self._write_status(
                    job_dir.name, "failed", error="서버 재시작으로 중단됨 — 재생성이 필요합니다"
                )

    # ── 제출 ──

    def submit_brief(self, brief: BriefInput) -> str:
        job_id = uuid.uuid4().hex[:12]
        job_dir = self.jobs_root / job_id
        job_dir.mkdir(parents=True)
        (job_dir / "brief.yaml").write_text(
            yaml.safe_dump(brief.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        self._write_status(job_id, "queued")
        self._queue.put(job_id)
        return job_id

    def rerun(self, job_id: str, new_seed: bool) -> str:
        """저장된 브리프로 재제출. 버전 핀 비교는 새 잡의 payload.json으로 남는다."""
        brief = self._read_brief(job_id)
        if new_seed:
            brief = brief.model_copy(update={"seed": "random"})
        new_id = self.submit_brief(brief)
        (self.jobs_root / new_id / "rerun_of.txt").write_text(job_id, encoding="utf-8")
        return new_id

    # ── 실행 (워커 전용 — 테스트는 직접 호출해 동기 검증) ──

    def cancel(self, job_id: str) -> str:
        """대기 잡은 즉시 취소, 실행 잡은 취소 플래그(+엔진 인터럽트는 워커가 수행)."""
        self._require_valid_job_id(job_id)
        job_dir = self.jobs_root / job_id
        status = self._read_json(job_dir / "status.json")
        if status is None:
            raise JobNotFound(job_id)
        state = status.get("state")
        if state == "queued":
            self._write_status(job_id, "canceled", error="대기 중 취소됨")
            return "canceled"
        if state in ("preparing", "running", "postprocess"):
            (job_dir / "cancel").write_text("1", encoding="utf-8")
            return "canceling"
        return state or "unknown"

    def process(self, job_id: str) -> None:
        job_dir = self.jobs_root / job_id
        current = self._read_json(job_dir / "status.json") or {}
        if current.get("state") == "canceled":
            return  # 대기 중 취소된 잡 — 워커가 건너뛴다
        try:
            brief = self._read_brief(job_id)
            build = build_job(
                brief,
                self.packs,
                library=AssetLibrary(self.assets_dir)
                if (brief.assets or brief.logo_reference)
                else None,
                profiles=self.profiles,
                job_id=job_id,
                workdir=job_dir / "inputs",
                engine_flavor=self.config.engine_flavor,
            )

            def progress(state: str, done: int, total: int) -> None:
                self._write_status(job_id, state, done=done, total=total)

            result = execute_job(
                build,
                self._engine_factory(),
                self.config,
                timeout_s=self._timeout_s,
                progress=progress,
                cancel_check=lambda: (job_dir / "cancel").is_file(),
            )
            self._write_status(
                job_id,
                "done",
                done=len(result.candidates),
                total=len(result.candidates),
                passed=result.report.passed,
                strategy=result.strategy,
            )
        except JobCancelledError:
            self._write_status(job_id, "canceled", error="사용자 취소")
        except Exception as exc:
            # 실패 사유를 화면에 그대로 보여야 하므로 광범위 캐치가 맞다
            log.exception("잡 실패: %s", job_id)
            self._write_status(job_id, "failed", error=str(exc)[:2000])

    # ── 조회 ──

    def queue_position(self, job_id: str) -> int | None:
        queued: list[tuple[float, str]] = []
        for job_dir in self.jobs_root.iterdir():
            if not job_dir.is_dir():
                continue
            status = self._read_json(job_dir / "status.json")
            if status and status.get("state") == "queued":
                queued.append((status.get("created_at", 0.0), job_dir.name))
        queued.sort()
        for position, (_, queued_id) in enumerate(queued):
            if queued_id == job_id:
                return position
        return None

    @staticmethod
    def _entry_prefix(entry: list) -> str | None:
        """큐 엔트리 그래프에서 SaveImage filename_prefix 추출 — 우리 잡은 pm_<job_id>."""
        try:
            graph = entry[2]
            for node in graph.values():
                if isinstance(node, dict) and node.get("class_type") == "SaveImage":
                    return node.get("inputs", {}).get("filename_prefix")
        except (IndexError, AttributeError, TypeError):
            pass
        return None

    def _engine_status(self, active: list[dict]) -> dict:
        """엔진 큐 상태. 웹 잡의 후보 프롬프트(pm_<id>)는 '외부'로 세지 않는다."""
        now = time.time()
        if self._engine_cache and now - self._engine_cache[0] < ENGINE_STATUS_TTL_S:
            return self._engine_cache[1]
        status = self._probe_engine(active)
        self._engine_cache = (now, status)
        return status

    def _probe_engine(self, active: list[dict]) -> dict:
        if self._status_engine is None:
            self._status_engine = self._engine_factory()
        snapshot = getattr(self._status_engine, "queue_snapshot", None)
        if snapshot is None:
            # 큐를 보고하지 않는 엔진(테스트 대역·HostedAPI 등)
            return {**UNREACHABLE_ENGINE_STATUS, "state": "unknown"}
        may_wake = getattr(self._status_engine, "probe_may_wake", False)
        if may_wake and not active:
            # 유휴 원격 인스턴스를 웹 폴링이 깨우면 GPU 과금이 시작된다 — 네트워크를 건드리지 않는다
            return {**UNREACHABLE_ENGINE_STATUS, "state": "idle"}
        result = snapshot(group=self._active_backend_group(active) if may_wake else None)
        if not result.get("reachable"):
            return {**UNREACHABLE_ENGINE_STATUS, "state": result.get("state", "down")}
        web_prefixes = {f"pm_{job['job_id']}" for job in active}
        return {
            "reachable": True,
            "busy": result["busy"],
            "running": result["running"],
            "pending": result["pending"],
            "external": sum(
                1 for e in result.get("entries", []) if self._entry_prefix(e) not in web_prefixes
            ),
            "state": "up",
        }

    def _active_backend_group(self, active: list[dict]) -> str | None:
        """진행 중 잡의 backend_group — 다른 그룹의 유휴 원격 인스턴스를 프로브가 깨우지 않게 한다."""
        # 실행 단계 잡을 우선한다 (queued는 payload.json이 아직 없을 수 있다)
        ordered = sorted(active, key=lambda job: job.get("state") == "queued")
        for job in ordered:
            payload = self._read_json(self.jobs_root / job["job_id"] / "payload.json") or {}
            group = payload.get("backend_group")
            if group:
                return group
        return None

    def queue_snapshot(self) -> dict:
        """실시간 패널용 — 실행 중 잡 + 대기열(생성순) + 엔진 상태. 완료·실패는 제외한다."""
        active: list[dict] = []
        for job_dir in self.jobs_root.iterdir():
            if not job_dir.is_dir():
                continue
            status = self._read_json(job_dir / "status.json")
            if not status or status.get("state") not in PENDING_STATES:
                continue
            brief_path = job_dir / "brief.yaml"
            brief = (
                yaml.safe_load(brief_path.read_text(encoding="utf-8"))
                if brief_path.is_file()
                else {}
            ) or {}
            style = brief.get("style") or {}
            active.append(
                {
                    "job_id": job_dir.name,
                    "state": status.get("state"),
                    "done": status.get("done", 0),
                    "total": status.get("total", 0),
                    "created_at": status.get("created_at"),
                    "campaign_text": brief.get("campaign_text", ""),
                    "object_concept": brief.get("object_concept", ""),
                    "free_text": style.get("free_text"),
                    "style_packs": style.get("style_packs") or [],
                    "model": brief.get("model"),
                    "seed": _seed_out(brief.get("seed")),
                    "candidate_count": brief.get("candidate_count"),
                }
            )
        running = [j for j in active if j["state"] != "queued"]
        queued = sorted(
            (j for j in active if j["state"] == "queued"),
            key=lambda j: j.get("created_at") or 0,
        )
        for position, job in enumerate(queued):
            job["queue_position"] = position
        return {
            "running": running[0] if running else None,
            "queued": queued,
            "engine": self._engine_status(active),
        }

    def detail(self, job_id: str) -> dict:
        self._require_valid_job_id(job_id)
        job_dir = self.jobs_root / job_id
        status = self._read_json(job_dir / "status.json")
        if status is None:
            raise JobNotFound(job_id)
        brief_path = job_dir / "brief.yaml"
        brief_raw = (
            yaml.safe_load(brief_path.read_text(encoding="utf-8"))
            if brief_path.is_file()
            else None
        )
        if isinstance(brief_raw, dict) and "seed" in brief_raw:
            brief_raw = {**brief_raw, "seed": _seed_out(brief_raw["seed"])}
        payload = self._read_json(job_dir / "payload.json") or {}
        qa = self._read_json(job_dir / "qa_report.json") or {}
        candidates = []
        for cand in qa.get("candidates", []):
            name = f"{cand['index']:02d}_{cand['seed']}.png"
            candidates.append(
                {
                    "index": cand["index"],
                    # 시드는 64비트라 JS Number(2^53)를 넘는다 — 숫자로 내보내면 브라우저에서
                    # 뭉개져 후보마다 같은 값으로 보인다. 문자열로 내보내 정확도를 지킨다.
                    "seed": _seed_out(cand["seed"]),
                    "passed": cand.get("passed", False),
                    "checks": cand.get("checks", []),
                    "final": f"final/{name}" if (job_dir / "final" / name).is_file() else None,
                    "candidate": f"candidates/{name}"
                    if (job_dir / "candidates" / name).is_file()
                    else None,
                }
            )
        rerun_of_path = job_dir / "rerun_of.txt"
        return {
            "job_id": job_id,
            "state": status.get("state"),
            "done": status.get("done", 0),
            "total": status.get("total", 0),
            "error": status.get("error"),
            "passed": status.get("passed"),
            "strategy": status.get("strategy"),
            "created_at": status.get("created_at"),
            "updated_at": status.get("updated_at"),
            "queue_position": self.queue_position(job_id)
            if status.get("state") == "queued"
            else None,
            "brief": brief_raw,
            "pins": {
                "workflow": payload.get("workflow"),
                "style_packs": payload.get("style_packs"),
                "model_id": (payload.get("model_manifest") or {}).get("model_id"),
                "matting_chain": payload.get("matting_chain"),
            },
            "candidates": candidates,
            "selection": self._read_json(job_dir / "selection.json"),
            "rerun_of": rerun_of_path.read_text(encoding="utf-8")
            if rerun_of_path.is_file()
            else None,
        }

    def select(self, job_id: str, indices: list[int]) -> None:
        self._require_valid_job_id(job_id)
        job_dir = self.jobs_root / job_id
        if not (job_dir / "status.json").is_file():
            raise JobNotFound(job_id)
        (job_dir / "selection.json").write_text(
            json.dumps(sorted(set(indices))), encoding="utf-8"
        )

    def image_path(self, job_id: str, kind: str, name: str) -> Path:
        """경로 탈출 방지 — job_id 형식 + kind 화이트리스트 + 파일명 검증."""
        self._require_valid_job_id(job_id)
        if kind not in IMAGE_KINDS or "/" in name or ".." in name or not name.endswith(".png"):
            raise JobNotFound(f"{job_id}/{kind}/{name}")
        path = self.jobs_root / job_id / kind / name
        if not path.is_file():
            raise JobNotFound(str(path))
        return path

    # ── 내부 ──

    def _read_brief(self, job_id: str) -> BriefInput:
        self._require_valid_job_id(job_id)
        path = self.jobs_root / job_id / "brief.yaml"
        if not path.is_file():
            raise JobNotFound(job_id)
        return BriefInput.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))

    def _write_status(
        self,
        job_id: str,
        state: str,
        done: int = 0,
        total: int = 0,
        error: str | None = None,
        passed: bool | None = None,
        strategy: str | None = None,
    ) -> None:
        path = self.jobs_root / job_id / "status.json"
        previous = self._read_json(path) or {}
        now = time.time()
        path.write_text(
            json.dumps(
                {
                    "state": state,
                    "done": done,
                    "total": total,
                    "error": error,
                    "passed": passed,
                    "strategy": strategy,
                    "created_at": previous.get("created_at", now),
                    "updated_at": now,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _require_valid_job_id(job_id: str) -> None:
        if not JOB_ID_RE.fullmatch(job_id):
            raise JobNotFound(job_id)

    @staticmethod
    def _read_json(path: Path) -> dict | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
