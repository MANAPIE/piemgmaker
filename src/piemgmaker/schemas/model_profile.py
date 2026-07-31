"""모델 프로파일 — 사용자가 고르는 생성 모델 계열(qwen-image / flux2-dev)의 단일 소스.

스타일 팩은 스타일(프롬프트·재질·그림자·매팅)만 소유하고, 어떤 모델·워크플로우·샘플링으로
그릴지는 프로파일이 결정한다. 팩의 workflow_template/model_manifest는 프로파일이 없을 때의
레거시 폴백으로만 쓰인다.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

from piemgmaker.schemas.style_pack import ModelFile, ModelManifest, SamplingSpec


class NativeAlphaSpec(BaseModel):
    """네이티브 알파(translucent 1순위) 전용 워크플로우·모델 — 지원 프로파일만 보유."""

    workflow: str
    manifest: ModelManifest
    sampling: SamplingSpec | None = None


class RemoteFileOverride(BaseModel):
    """원격 백엔드 전용 모델 파일 치환 — 로컬 파일명(replaces)을 원격 파일명(name)으로 바꾼다.

    원격 GPU 메모리에 맞춘 양자화 변형(Q6_K 등)을 쓰기 위한 것이며,
    sha256은 파일을 받아 핀하기 전까지 비워 둘 수 있다.
    """

    replaces: str
    name: str
    sha256: str | None = None


class RemoteSpec(BaseModel):
    """프로파일의 원격 백엔드 지원 선언 — 이 블록이 없으면 원격 실행 대상이 아니다."""

    backend_group: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    files: list[RemoteFileOverride] = Field(default_factory=list)
    supports_styleref: bool = False
    supports_native_alpha: bool = False

    @model_validator(mode="after")
    def _check_files(self) -> "RemoteSpec":
        targets = [f.replaces for f in self.files]
        if len(targets) != len(set(targets)):
            raise ValueError(f"remote.files의 치환 대상 중복: {targets}")
        return self

    def apply(self, manifest: ModelManifest) -> ModelManifest:
        """로컬 매니페스트에 원격 파일 치환을 적용한 사본을 만든다.

        매칭되는 치환만 적용한다 — 프로파일의 매니페스트(base·styleref·native_alpha)마다
        unet이 달라, 한 files 목록이 세 매니페스트의 치환을 함께 담기 때문이다.
        치환 대상 오타는 ModelProfile 로드 시점 검증이 잡는다.
        """
        if not self.files:
            return manifest
        overrides = {f.replaces: f for f in self.files}
        files = []
        for file in manifest.files:
            override = overrides.get(file.name)
            files.append(
                ModelFile(name=override.name, kind=file.kind, sha256=override.sha256)
                if override
                else file
            )
        return manifest.model_copy(update={"files": files})


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
    remote: RemoteSpec | None = None  # 원격 백엔드 지원 선언 (없으면 로컬 전용 프로파일)

    @model_validator(mode="after")
    def _check_remote_overrides(self) -> "ModelProfile":
        # apply()가 매칭만 적용하는 대신, 오타 탐지는 여기서 한다 — 모든 치환 대상이
        # 이 프로파일의 매니페스트 중 최소 한 곳에는 존재해야 한다.
        if self.remote is None or not self.remote.files:
            return self
        known: set[str] = {f.name for f in self.manifest.files}
        if self.styleref_manifest is not None:
            known |= {f.name for f in self.styleref_manifest.files}
        if self.native_alpha is not None:
            known |= {f.name for f in self.native_alpha.manifest.files}
        unknown = sorted({f.replaces for f in self.remote.files} - known)
        if unknown:
            raise ValueError(
                f"{self.id!r}의 remote.files 치환 대상이 어느 매니페스트에도 없습니다: {unknown}"
            )
        return self


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
