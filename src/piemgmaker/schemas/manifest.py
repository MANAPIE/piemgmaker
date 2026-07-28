"""자가 보고 규격 — CLI manifest 명령과 서버 /api/manifest가 동일 규격으로 노출."""

from typing import Literal

from pydantic import BaseModel, Field

from piemgmaker.config import Config
from piemgmaker.schemas.generation import WorkflowRef
from piemgmaker.schemas.style_pack import MaterialClass, MattingStrategyId, StylePack


class MattingModelInfo(BaseModel):
    strategy: MattingStrategyId
    model: str
    hash: str | None = None


class StylePackInfo(BaseModel):
    id: str
    version: str
    material_class: MaterialClass
    workflow_template: str
    model_pinned: bool


class ManifestReport(BaseModel):
    service: Literal["piemgmaker"] = "piemgmaker"
    package_version: str
    engine: str
    backend_url: str
    backend_auth_set: bool  # 시크릿 미노출 — 설정 여부만 보고
    storage: str
    workflows: list[WorkflowRef] = Field(default_factory=list)
    style_packs: list[StylePackInfo] = Field(default_factory=list)
    matting_models: list[MattingModelInfo] = Field(default_factory=list)


def build_manifest(
    config: Config,
    engine_name: str,
    package_version: str,
    workflows: list[WorkflowRef],
    style_packs: dict[str, StylePack],
) -> ManifestReport:
    matting_seen: dict[tuple[str, str], MattingModelInfo] = {}
    for pack in style_packs.values():
        key = (pack.matting.strategy, pack.matting.model)
        matting_seen.setdefault(
            key,
            MattingModelInfo(
                strategy=pack.matting.strategy,
                model=pack.matting.model,
                hash=pack.matting.hash,
            ),
        )
    return ManifestReport(
        package_version=package_version,
        engine=engine_name,
        backend_url=config.backend_url,
        backend_auth_set=bool(config.backend_auth),
        storage=str(config.storage),
        workflows=workflows,
        style_packs=[
            StylePackInfo(
                id=p.id,
                version=p.version,
                material_class=p.material_class,
                workflow_template=p.workflow_template,
                model_pinned=p.model_manifest.pinned,
            )
            for p in style_packs.values()
        ],
        matting_models=list(matting_seen.values()),
    )
