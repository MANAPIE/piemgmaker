"""RemoteComfy 어댑터 — 원격 ComfyUI 백엔드용 스텁. 규격만 예약(미구현)."""

from pathlib import Path

from piemgmaker.engine.contract import CandidateResult, JobHandle, JobStatus
from piemgmaker.schemas.generation import JobPayload

_NOT_IMPLEMENTED = "RemoteComfy 어댑터는 아직 구현되지 않았습니다"


class RemoteComfyEngine:
    name = "remote-comfy"

    def submit(self, payload: JobPayload) -> JobHandle:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    def poll(self, handle: JobHandle) -> JobStatus:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    def fetch(self, handle: JobHandle, dest_dir: Path) -> list[CandidateResult]:
        raise NotImplementedError(_NOT_IMPLEMENTED)
