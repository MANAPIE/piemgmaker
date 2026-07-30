"""프로파일 × 엔진 → 유효 능력 판정.

build_job의 실패 판정과 /api/models 응답이 이 함수를 공유한다. 두 곳이 각자 판정하면
어긋나고, 그 어긋남은 "UI는 쓸 수 있다고 표시하는데 제출하면 실패"로 나타난다.
"""

from dataclasses import dataclass

from piemgmaker.config import EngineFlavor
from piemgmaker.schemas.model_profile import ModelProfile


@dataclass(frozen=True)
class ModelCapability:
    """현재 엔진에서 이 프로파일로 실제 할 수 있는 것."""

    model_id: str
    supports_styleref: bool
    supports_inpaint: bool
    supports_native_alpha: bool


def model_capability(profile: ModelProfile, engine_flavor: EngineFlavor) -> ModelCapability:
    # 원격은 생성·인페인팅만 지원한다. 스타일 참조(Edit 계열)와 네이티브 알파(Layered 계열)는
    # remote 블록이 명시적으로 허용해야 쓸 수 있고, 블록이 없으면 원격 실행 자체가 불가능하다.
    if engine_flavor == "remote" and profile.remote is None:
        return ModelCapability(profile.id, False, False, False)

    remote = profile.remote if engine_flavor == "remote" else None
    styleref = bool(profile.workflow_styleref)
    native_alpha = profile.native_alpha is not None
    if remote is not None:
        styleref = styleref and remote.supports_styleref
        native_alpha = native_alpha and remote.supports_native_alpha

    return ModelCapability(
        model_id=profile.id,
        supports_styleref=styleref,
        supports_inpaint=bool(profile.workflow_inpaint),
        supports_native_alpha=native_alpha,
    )
