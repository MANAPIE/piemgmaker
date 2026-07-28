"""잡 페이로드 — 히스토리 '이 설정으로 다시 생성'이 그대로 재사용하는 재현 단위."""

from pydantic import BaseModel, Field, computed_field

from piemgmaker.schemas.brief import SizeSpec
from piemgmaker.schemas.style_pack import MattingStrategyId, ModelManifest


class WorkflowRef(BaseModel):
    id: str
    hash: str  # 템플릿 파일 sha256


class StylePackRef(BaseModel):
    id: str
    version: str


class Placement(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    scale: float = Field(gt=0)


class ResolvedAsset(BaseModel):
    asset_id: str
    variant_id: str
    file: str
    file_sha256: str
    placement: Placement


class Prompts(BaseModel):
    positive: str
    negative: str = ""


class JobPayload(BaseModel):
    job_id: str
    workflow: WorkflowRef
    graph: dict  # 렌더 완료된 ComfyUI API 그래프 (시드는 제출 시 후보별 주입)
    model_manifest: ModelManifest
    style_packs: list[StylePackRef] = Field(default_factory=list)
    matting_chain: list[MattingStrategyId] = Field(min_length=1)
    seeds: list[int] = Field(min_length=1, max_length=8)
    size: SizeSpec
    prompts: Prompts
    assets: list[ResolvedAsset] = Field(default_factory=list)
    # 그래프에 남겨둔 입력 이미지 슬롯(예: canvas, mask_image) → 로컬 파일 경로.
    # 업로드 방식이 엔진마다 달라 제출 시점에 어댑터가 채운다.
    input_images: dict[str, str] = Field(default_factory=dict)
    # 워크플로우가 여러 장을 출력할 때 회수할 인덱스 — 네이티브 알파(Layered)는
    # [컴포지트, 레이어...] 순서라 마지막(-1)이 투명 오브젝트 레이어다
    output_index: int = 0

    @computed_field
    @property
    def protected_asset(self) -> bool:
        """자산 포함 잡 — HostedAPI 라우팅 금지 판정에 쓰인다."""
        return bool(self.assets)
