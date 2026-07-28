"""StylePack v1 — material_class가 배경 제거 전략 라우팅을 결정한다."""

from pathlib import Path
from string import Formatter
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

MaterialClass = Literal["opaque", "translucent"]
ShadowPolicy = Literal["none", "soft_floor", "ambient"]
MattingStrategyId = Literal["segment", "native_alpha", "trimap"]

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
