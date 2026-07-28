"""자산 라이브러리 v1 스키마 — 자산은 생성하지 않고 삽입만 한다."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

ID_PATTERN = r"^[a-z0-9][a-z0-9-]*$"


class AssetUsage(BaseModel):
    min_scale: float = Field(default=0.1, gt=0)
    clear_space_px: int = Field(default=0, ge=0)


class AssetVariant(BaseModel):
    id: str = Field(pattern=ID_PATTERN)
    file: str  # manifest.yaml 위치 기준 상대 경로 (알파 PNG 권장)
    tags: list[str] = Field(default_factory=list)
    archived: bool = False  # soft delete — 파일은 남기고 신규 사용만 차단


class Asset(BaseModel):
    id: str = Field(pattern=ID_PATTERN)
    name: str
    type: Literal["logo", "object"]
    variants: list[AssetVariant] = Field(min_length=1)
    usage: AssetUsage = Field(default_factory=AssetUsage)
    archived: bool = False  # soft delete

    @field_validator("variants")
    @classmethod
    def _unique_variant_ids(cls, variants: list[AssetVariant]) -> list[AssetVariant]:
        ids = [v.id for v in variants]
        if len(ids) != len(set(ids)):
            raise ValueError(f"variant id 중복: {ids}")
        return variants


class AssetManifest(BaseModel):
    assets: list[Asset] = Field(default_factory=list)

    @field_validator("assets")
    @classmethod
    def _unique_asset_ids(cls, assets: list[Asset]) -> list[Asset]:
        ids = [a.id for a in assets]
        if len(ids) != len(set(ids)):
            raise ValueError(f"asset id 중복: {ids}")
        return assets
