"""엔진 계약 — 워크플로우 실행기 submit/poll/fetch."""

from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from piemgmaker.schemas.generation import JobPayload

JobState = Literal["queued", "preparing", "running", "done", "failed"]


class EngineError(Exception):
    pass


class EngineDisabledError(EngineError):
    pass


class ProtectedAssetRoutingError(EngineError):
    pass


class JobFailedError(EngineError):
    pass


class JobTimeoutError(EngineError):
    pass


class JobCancelledError(EngineError):
    """사용자 취소 — 실패가 아니라 별도 종결 상태로 기록한다."""


class JobHandle(BaseModel):
    engine: str
    job_id: str
    seeds: list[int] = Field(min_length=1)
    remote_ids: list[str] = Field(min_length=1)
    output_index: int = 0  # payload.output_index 계승 — fetch가 회수할 출력 이미지
    # payload.backend_group 계승 — 원격 백엔드가 여러 서비스로 나뉠 때 후속 요청의 라우팅 키
    engine_group: str | None = None


class JobStatus(BaseModel):
    state: JobState
    done_count: int = 0
    total: int = 0
    error: str | None = None


class CandidateResult(BaseModel):
    index: int
    seed: int
    path: Path


class WorkflowEngine(Protocol):
    name: str

    def submit(self, payload: JobPayload) -> JobHandle: ...

    def poll(self, handle: JobHandle) -> JobStatus: ...

    def fetch(self, handle: JobHandle, dest_dir: Path) -> list[CandidateResult]: ...

    def cancel(self, handle: JobHandle) -> None: ...
