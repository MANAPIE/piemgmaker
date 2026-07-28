"""material_class → 배경 제거 전략 폴백 체인 + 전략별 노드 배선 레지스트리."""

from dataclasses import dataclass, field

from piemgmaker.schemas.style_pack import MaterialClass, MattingStrategyId

STRATEGY_CHAINS: dict[MaterialClass, list[MattingStrategyId]] = {
    # 단색 배경 강제 txt2img → 세그먼트 매팅
    "opaque": ["segment"],
    # 네이티브 알파 1순위, trimap 폴백. 소진 시 잡 실패 + 재생성 힌트
    "translucent": ["native_alpha", "trimap"],
}

# 조각 파일명 매핑. native_alpha는 후처리 조각이 아니라 전용 워크플로우 변형이라 None —
# 전용 워크플로우 object-gen-native-alpha-v1 템플릿으로 처리한다
STRATEGY_FRAGMENTS: dict[MattingStrategyId, str | None] = {
    "segment": "matting_segment",
    "trimap": "matting_trimap",
    "native_alpha": None,
}


class MattingNodeNotConfigured(RuntimeError):
    pass


def chain_for(material: MaterialClass) -> list[MattingStrategyId]:
    return list(STRATEGY_CHAINS[material])


@dataclass(frozen=True)
class MattingNodeConfig:
    """전략을 실행 가능하게 만드는 배선 — 조각 슬롯 값(노드 class·모델명 등)."""

    slots: dict[str, object] = field(default_factory=dict)


# 기본 노드 배선: ComfyUI-RMBG(BiRefNetRMBG) + ComfyUI-Image-Matting(ViTMatte).
# 모델 변형·커널 값은 slots로 주입하며 스타일 팩 matting.hash로 핀할 수 있다.
DEFAULT_NODE_CONFIGS: dict[MattingStrategyId, MattingNodeConfig] = {
    "segment": MattingNodeConfig(slots={"segment_model": "BiRefNet-matting"}),
    "trimap": MattingNodeConfig(
        slots={
            "trimap_coarse_model": "BiRefNet-general",
            "trimap_kernel": 9,
            # ComfyUI-Image-Matting 노드의 실제 choice 문자열 (/object_info 확인값)
            "trimap_model": "vitmatte_small (103 MB)",
        }
    ),
    # native_alpha는 전용 워크플로우(object-gen-native-alpha-v1, Qwen-Image-Layered)로 배선
}

# 이 워크플로우로 빌드된 잡은 native_alpha 전략을 그래프 변경 없이 소화한다
NATIVE_ALPHA_WORKFLOWS = {"object-gen-native-alpha-v1"}


class MattingNodeRegistry:
    """기본값은 표준 노드 배선. 미배선 전략(native_alpha) 실행은 명시적으로 실패."""

    def __init__(self, configs: dict[MattingStrategyId, MattingNodeConfig] | None = None):
        self._configs = dict(DEFAULT_NODE_CONFIGS if configs is None else configs)

    def configured(self, strategy: MattingStrategyId) -> bool:
        return strategy in self._configs

    def get(self, strategy: MattingStrategyId) -> MattingNodeConfig:
        if strategy not in self._configs:
            raise MattingNodeNotConfigured(
                f"매팅 전략 {strategy!r}의 노드가 미확정입니다 — "
                "레지스트리에 노드 배선을 등록하세요"
            )
        return self._configs[strategy]
