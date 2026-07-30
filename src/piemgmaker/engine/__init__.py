from piemgmaker.config import ENGINE_LOCAL, ENGINE_REMOTE, Config, ConfigError
from piemgmaker.engine.comfy_http import ComfyHTTPEngine
from piemgmaker.engine.contract import (
    CandidateResult,
    EngineDisabledError,
    EngineError,
    JobCancelledError,
    JobFailedError,
    JobHandle,
    JobStatus,
    JobTimeoutError,
    ProtectedAssetRoutingError,
    WorkflowEngine,
)
from piemgmaker.engine.hosted_api import HostedAPIEngine
from piemgmaker.engine.local_comfy import LocalComfyEngine
from piemgmaker.engine.remote_comfy import RemoteComfyEngine

# PM_ENGINE 값 → 어댑터. 키는 각 어댑터의 name과 같다
ENGINE_REGISTRY: dict[str, type[ComfyHTTPEngine]] = {
    ENGINE_LOCAL: LocalComfyEngine,
    ENGINE_REMOTE: RemoteComfyEngine,
}


def create_engine(config: Config) -> WorkflowEngine:
    engine_cls = ENGINE_REGISTRY.get(config.engine)
    if engine_cls is None:
        raise ConfigError(
            f"알 수 없는 엔진: {config.engine!r} (지원: {sorted(ENGINE_REGISTRY)})"
        )
    return engine_cls(config)


__all__ = [
    "ENGINE_REGISTRY",
    "CandidateResult",
    "ComfyHTTPEngine",
    "EngineDisabledError",
    "EngineError",
    "HostedAPIEngine",
    "JobCancelledError",
    "JobFailedError",
    "JobHandle",
    "JobStatus",
    "JobTimeoutError",
    "LocalComfyEngine",
    "ProtectedAssetRoutingError",
    "RemoteComfyEngine",
    "WorkflowEngine",
    "create_engine",
]
