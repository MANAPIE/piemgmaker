"""HostedAPI 어댑터 — 기본 비활성. 보호 자산 잡 라우팅은 활성 여부와 무관하게 금지."""

from pathlib import Path

from piemgmaker.engine.contract import (
    CandidateResult,
    EngineDisabledError,
    JobHandle,
    JobStatus,
    ProtectedAssetRoutingError,
)
from piemgmaker.schemas.generation import JobPayload


class HostedAPIEngine:
    name = "hosted-api"

    def __init__(self, enabled: bool = False):
        self._enabled = enabled

    def submit(self, payload: JobPayload) -> JobHandle:
        if payload.protected_asset:
            raise ProtectedAssetRoutingError(
                "보호 자산 잡은 HostedAPI로 라우팅할 수 없습니다"
            )
        if not self._enabled:
            raise EngineDisabledError("HostedAPI 어댑터는 기본 비활성입니다")
        raise NotImplementedError("HostedAPI 실 구현은 아직 제공되지 않습니다")

    def poll(self, handle: JobHandle) -> JobStatus:
        raise NotImplementedError("HostedAPI 실 구현은 아직 제공되지 않습니다")

    def fetch(self, handle: JobHandle, dest_dir: Path) -> list[CandidateResult]:
        raise NotImplementedError("HostedAPI 실 구현은 아직 제공되지 않습니다")
