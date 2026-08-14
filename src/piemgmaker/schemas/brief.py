"""BriefInput v2 — 생성 요청 입력 스키마."""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

SIZE_PRESETS: dict[str, tuple[int, int]] = {
    "1:1": (1024, 1024),
    "4:3": (1152, 864),
    "16:9": (1344, 768),
    "3:4": (864, 1152),
}
DEFAULT_SIZE_PRESET = "1:1"

# 커스텀 사이즈 경계 — latent 제약으로 8의 배수 강제.
SIZE_MIN = 256
SIZE_MAX = 2048
SIZE_MULTIPLE = 8

ASSET_REF_PATTERN = r"^[a-z0-9][a-z0-9-]*(:[a-z0-9][a-z0-9-]*)?$"
ASSET_REF_MAX_LEN = 128  # 패턴은 길이를 제한하지 않으므로 저장 바이트 상한을 별도로 둔다


class SizeSpec(BaseModel):
    width: int
    height: int

    @model_validator(mode="after")
    def _check_bounds(self) -> "SizeSpec":
        for side in (self.width, self.height):
            if not SIZE_MIN <= side <= SIZE_MAX:
                raise ValueError(f"사이즈는 {SIZE_MIN}~{SIZE_MAX} 범위여야 합니다: {side}")
            if side % SIZE_MULTIPLE != 0:
                raise ValueError(f"사이즈는 {SIZE_MULTIPLE}의 배수여야 합니다: {side}")
        return self


class StyleInput(BaseModel):
    style_packs: list[str] = Field(default_factory=list)
    free_text: str | None = Field(default=None, max_length=500)

    @property
    def consistency_guaranteed(self) -> bool:
        """팩 혼합 또는 자유 입력 사용 시 '스타일 일관성 비보증' 라벨 대상."""
        return len(self.style_packs) <= 1 and not self.free_text


class PlacementHint(BaseModel):
    asset_position: Literal["auto", "center", "left", "right", "top", "bottom"] = "auto"
    composition: str | None = Field(default=None, max_length=200)


class BriefInput(BaseModel):
    campaign_text: str = Field(min_length=1, max_length=500)
    object_concept: str = Field(min_length=1, max_length=300)
    # 생성 모델 프로파일 id (None = 기본 프로파일) — model_profiles.yaml 참조
    model: str | None = None
    assets: list[str] = Field(default_factory=list)
    size_preset: str | SizeSpec = DEFAULT_SIZE_PRESET
    style: StyleInput = Field(default_factory=StyleInput)
    candidate_count: int = Field(default=4, ge=1, le=8)
    seed: int | Literal["random"] = "random"
    placement_hint: PlacementHint | None = None
    negative: str | None = Field(default=None, max_length=500)
    reference_images: list[str] = Field(default_factory=list, max_length=2)
    # 생성 단계 로고 통합(Edit 계열) — 로고를 참조 '내용'으로 재현한다.
    # 합성 경로(assets)와 달리 픽셀·코어 보증이 없다 (원칙 1의 명시적 예외 트랙).
    logo_reference: str | None = None
    # 자산 합성 모드 오버라이드 — None이면 팩 blend 설정을 따른다.
    # overlay=조명·그림자 정합, imprint=표면 새김(질감 투과)
    asset_blend_mode: Literal["overlay", "imprint"] | None = None

    @field_validator("assets")
    @classmethod
    def _check_asset_refs(cls, refs: list[str]) -> list[str]:
        for ref in refs:
            if len(ref) > ASSET_REF_MAX_LEN or not re.fullmatch(ASSET_REF_PATTERN, ref):
                raise ValueError(f"자산 참조 형식 오류(asset-id[:variant-id]): {ref[:64]!r}")
        return refs

    @field_validator("logo_reference")
    @classmethod
    def _check_logo_ref(cls, ref: str | None) -> str | None:
        if ref is not None and (
            len(ref) > ASSET_REF_MAX_LEN or not re.fullmatch(ASSET_REF_PATTERN, ref)
        ):
            raise ValueError(f"로고 참조 형식 오류(asset-id[:variant-id]): {ref[:64]!r}")
        return ref

    @field_validator("size_preset")
    @classmethod
    def _check_preset(cls, v: str | SizeSpec) -> str | SizeSpec:
        if isinstance(v, str) and v not in SIZE_PRESETS:
            raise ValueError(f"알 수 없는 사이즈 프리셋: {v!r} (지원: {sorted(SIZE_PRESETS)})")
        return v

    @field_validator("seed")
    @classmethod
    def _check_seed(cls, v: int | str) -> int | str:
        if isinstance(v, int) and not 0 <= v < 2**63:
            raise ValueError("시드는 0 이상 2^63 미만이어야 합니다")
        return v

    def size(self) -> SizeSpec:
        if isinstance(self.size_preset, SizeSpec):
            return self.size_preset
        width, height = SIZE_PRESETS[self.size_preset]
        return SizeSpec(width=width, height=height)
