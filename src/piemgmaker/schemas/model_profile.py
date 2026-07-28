"""모델 프로파일 — 사용자가 고르는 생성 모델 계열(qwen-image / flux2-dev)의 단일 소스.

스타일 팩은 스타일(프롬프트·재질·그림자·매팅)만 소유하고, 어떤 모델·워크플로우·샘플링으로
그릴지는 프로파일이 결정한다. 팩의 workflow_template/model_manifest는 프로파일이 없을 때의
레거시 폴백으로만 쓰인다.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

from piemgmaker.schemas.style_pack import ModelManifest, SamplingSpec


class NativeAlphaSpec(BaseModel):
    """네이티브 알파(translucent 1순위) 전용 워크플로우·모델 — 지원 프로파일만 보유."""

    workflow: str
    manifest: ModelManifest
    sampling: SamplingSpec | None = None


class ModelProfile(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    label: str
    workflow_gen: str
    workflow_inpaint: str | None = None
    workflow_styleref: str | None = None
    supports_negative: bool = True
    sampling: SamplingSpec
    manifest: ModelManifest
    styleref_manifest: ModelManifest | None = None  # 참조 이미지 경로가 다른 모델을 쓰는 경우(Edit 계열)
    native_alpha: NativeAlphaSpec | None = None


class ModelProfileRegistry(BaseModel):
    default: str
    profiles: list[ModelProfile] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> "ModelProfileRegistry":
        ids = [p.id for p in self.profiles]
        if len(ids) != len(set(ids)):
            raise ValueError(f"프로파일 id 중복: {ids}")
        if self.default not in ids:
            raise ValueError(f"default 프로파일 {self.default!r}이 목록에 없습니다")
        return self

    def get(self, profile_id: str | None) -> ModelProfile:
        wanted = profile_id or self.default
        for profile in self.profiles:
            if profile.id == wanted:
                return profile
        raise KeyError(f"알 수 없는 모델 프로파일: {wanted!r} (지원: {[p.id for p in self.profiles]})")


def load_model_profiles(path: Path) -> ModelProfileRegistry:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return ModelProfileRegistry.model_validate(data)
