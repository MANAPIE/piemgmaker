"""StylePack v1 — material_class가 배경 제거 전략 라우팅을 결정한다."""

from pathlib import Path
from string import Formatter
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

MaterialClass = Literal["opaque", "translucent"]
ShadowPolicy = Literal["none", "soft_floor", "ambient"]
MattingStrategyId = Literal["segment", "native_alpha", "trimap"]
TiltPreset = Literal["none", "slight", "iso", "strong"]
MaskRamp = Literal["linear", "smoothstep"]
BlendMode = Literal["overlay", "imprint"]

PROMPT_SLOTS = {"subject", "mood"}


class LoraRef(BaseModel):
    name: str
    weight: float = 1.0


class ModelFile(BaseModel):
    name: str
    kind: Literal["checkpoint", "unet", "clip", "vae", "lora", "matting"]
    sha256: str | None = None  # unpinned 허용 — manifest에 핀 여부 그대로 노출


class ModelManifest(BaseModel):
    model_id: str
    files: list[ModelFile] = Field(default_factory=list)

    @property
    def pinned(self) -> bool:
        return bool(self.files) and all(f.sha256 for f in self.files)


class MattingSpec(BaseModel):
    strategy: MattingStrategyId
    model: str
    hash: str | None = None


class JudgeCriteria(BaseModel):
    """VLM 스타일 QA — 스키마만 예약(미사용)."""

    vlm_prompt: str
    pass_score: float


class SamplingSpec(BaseModel):
    """모델별 샘플링 파라미터 — 미지정 시 오케스트레이터 기본값."""

    steps: int = Field(ge=1, le=100)
    cfg: float = Field(gt=0)


class GeometrySpec(BaseModel):
    """자산 기하 변환 — 팩이 각도를 소유한다 (잡별 오버라이드는 범위 밖)."""

    rotation_deg: float = Field(default=0.0, ge=-180, le=180)
    tilt: TiltPreset = "none"


class GenerativeBlendSpec(BaseModel):
    """인페인팅 마스크의 재통합 강도 — core_noise 0이면 코어를 완전 보호한다."""

    core_noise: int = Field(default=0, ge=0, le=128)
    band_px: int = Field(default=8, ge=1, le=64)
    ramp: MaskRamp = "linear"


class FidelitySpec(BaseModel):
    """하모나이즈 후 자산 편차 상한 — 초과 시 해당 후보만 force 합성으로 강등."""

    shape_iou_min: float = Field(default=0.98, ge=0, le=1)
    mean_delta_e_max: float = Field(default=12.0, gt=0)
    hue_shift_max_deg: float = Field(default=8.0, gt=0)


class BlendSpec(BaseModel):
    """자산 하모나이즈 강도 — 블록이 없으면(None) force 합성(코어 픽셀 보존).

    mode="imprint"는 장면 질감·음영이 로고를 관통하는 '새김'이라 색차가 설계상
    커진다 — imprint 팩은 fidelity.mean_delta_e_max를 함께 올려야 한다.
    """

    strength: float = Field(default=0.6, ge=0, le=1)
    mode: BlendMode = "overlay"
    relight: float = Field(default=0.5, ge=0, le=1)
    contact_shadow: float = Field(default=0.6, ge=0, le=1)
    drop_shadow: float = Field(default=0.35, ge=0, le=1)
    light_wrap: float = Field(default=0.3, ge=0, le=1)
    grain_match: bool = True
    imprint_texture: float = Field(default=0.85, ge=0, le=1)
    imprint_emboss: float = Field(default=0.5, ge=0, le=1)
    ink_opacity: float = Field(default=0.9, ge=0.5, le=1)
    geometry: GeometrySpec = Field(default_factory=GeometrySpec)
    generative: GenerativeBlendSpec = Field(default_factory=GenerativeBlendSpec)
    fidelity: FidelitySpec = Field(default_factory=FidelitySpec)


class StylePack(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    version: str
    name: str | None = None  # 화면 표시명 (미지정 시 id)
    prompt_template: str
    negative: str = ""
    palette: list[str] = Field(default_factory=list)
    reference_set: list[str] = Field(default_factory=list)
    loras: list[LoraRef] = Field(default_factory=list)
    material_class: MaterialClass
    shadow_policy: ShadowPolicy
    workflow_template: str
    model_manifest: ModelManifest
    matting: MattingSpec
    sampling: SamplingSpec | None = None
    blend: BlendSpec | None = None
    judge_criteria: JudgeCriteria | None = None

    @field_validator("prompt_template")
    @classmethod
    def _check_slots(cls, v: str) -> str:
        slots = {name for _, name, _, _ in Formatter().parse(v) if name}
        if "subject" not in slots:
            raise ValueError("prompt_template에 {subject} 슬롯이 필요합니다")
        unknown = slots - PROMPT_SLOTS
        if unknown:
            raise ValueError(f"허용되지 않은 슬롯: {sorted(unknown)} (허용: {sorted(PROMPT_SLOTS)})")
        return v

    @property
    def display_name(self) -> str:
        return self.name or self.id

    def render_prompt(self, subject: str, mood: str = "") -> str:
        return " ".join(self.prompt_template.format(subject=subject, mood=mood).split())


def load_style_pack(path: Path) -> StylePack:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return StylePack.model_validate(data)


def load_style_packs(directory: Path) -> dict[str, StylePack]:
    packs: dict[str, StylePack] = {}
    for path in sorted(directory.glob("*.yaml")):
        pack = load_style_pack(path)
        if pack.id in packs:
            raise ValueError(f"스타일 팩 id 중복: {pack.id} ({path})")
        packs[pack.id] = pack
    return packs
