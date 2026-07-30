"""브리프 → 잡 페이로드 → 엔진 실행 → 결정론 후단.

폴백 규칙:
- 배경 제거 전략은 material_class 체인 순서로 시도하고, 품질 실패(잡 오류·전 후보
  전경 미검출)에서만 다음 전략으로 넘어간다. 미배선(MattingNodeNotConfigured)은
  설정 문제이므로 그대로 올린다.
- 코어 해시 불일치는 파생 시드로 1회 재시도한다. 동일 시드 재실행은
  동일 결과가 나와 무의미하기 때문이다.
"""

import secrets
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
from PIL import Image
from pydantic import ValidationError

from piemgmaker.assets_lib.library import AssetLibrary, file_sha256
from piemgmaker.config import Config, EngineFlavor
from piemgmaker.pipeline.capabilities import model_capability
from piemgmaker.engine.contract import (
    CandidateResult,
    JobCancelledError,
    JobFailedError,
    JobTimeoutError,
    WorkflowEngine,
)
from piemgmaker.pipeline.matting_router import (
    NATIVE_ALPHA_WORKFLOWS,
    STRATEGY_FRAGMENTS,
    MattingNodeNotConfigured,
    MattingNodeRegistry,
    chain_for,
)
from piemgmaker.pipeline.paste_back import (
    PlacementOutOfBounds,
    make_inpaint_mask,
    paste_back,
    scale_asset,
    verify_core_hash,
)
from piemgmaker.pipeline.postprocess import (
    FORCED_BG_COLOR,
    ForegroundNotDetected,
    PostprocessSpec,
    estimate_bg_color,
    postprocess,
)
from piemgmaker.pipeline.qa import RULE_CORE_HASH, RULE_FOREGROUND, QAThresholds, run_qa
from piemgmaker.schemas.brief import BriefInput, SizeSpec
from piemgmaker.schemas.model_profile import ModelProfileRegistry
from piemgmaker.schemas.generation import (
    JobPayload,
    Placement,
    Prompts,
    ResolvedAsset,
    StylePackRef,
    WorkflowRef,
)
from piemgmaker.schemas.qa import CandidateQA, QACheck, QAReport
from piemgmaker.schemas.style_pack import (
    MaterialClass,
    MattingStrategyId,
    ModelManifest,
    SamplingSpec,
    StylePack,
)
from piemgmaker.workflows.render import (
    META_KEY,
    compose,
    fill_slots,
    load_fragment,
    load_template,
    strip_meta,
    unfilled_slots,
)

# 샘플링 기본값 — 프로파일/팩이 값을 주지 않을 때만 사용
DEFAULT_STEPS = 28
DEFAULT_CFG = 4.5
DEFAULT_INPAINT_DENOISE = 0.85

# 그림자는 스타일 팩 속성(shadow_policy)이 프롬프트로 반영된다
SHADOW_PROMPTS = {
    "none": "no shadow",
    "soft_floor": "resting with a soft floor contact shadow",
    "ambient": "subtle ambient occlusion shading",
}
OPAQUE_BG_PROMPT = "isolated on a plain solid neutral gray background"
TRANSLUCENT_BG_PROMPT = "isolated single object, clean transparent background"
STYLEREF_PROMPT = "render in the visual style of the reference image, do not copy its content"

RETRY_SEED_OFFSET = 1_000_003
ASSET_ROW_GAP_PX = 16  # 다중 자산 가로 일렬 배치 간격(px)
SEED_SPACE = 2**63

# 자산 경로: gen 워크플로우 → 대응 인페인팅 변형 (네이티브 알파는 인페인트 미지원)
INPAINT_VARIANTS = {
    "object-gen-v1": "object-inpaint-v1",
    "object-gen-qwen-v1": "object-inpaint-qwen-v1",
}


class OrchestrationError(ValueError):
    pass


@dataclass
class AssetLayer:
    resolved: ResolvedAsset
    rgba: np.ndarray  # placement.scale 적용 완료된 RGBA


def resolve_seeds(brief: BriefInput) -> list[int]:
    master = brief.seed if isinstance(brief.seed, int) else secrets.randbelow(SEED_SPACE)
    return [(master + i) % SEED_SPACE for i in range(brief.candidate_count)]


def build_prompts(
    brief: BriefInput,
    packs: list[StylePack],
    material: MaterialClass,
    has_reference: bool = False,
) -> Prompts:
    """{subject}=object_concept, {mood}=style.free_text.

    campaign_text는 컨셉 참고 전용이라 프롬프트에 넣지 않는다.
    팩이 없으면(참조 이미지 단독 등) 소재+자유 텍스트만으로 구성한다.
    """
    subject = brief.object_concept
    mood = brief.style.free_text or ""
    if packs:
        primary = packs[0]
        parts = [primary.render_prompt(subject=subject, mood=mood)]
        parts.extend(p.render_prompt(subject=subject, mood="") for p in packs[1:])
        parts.append(SHADOW_PROMPTS[primary.shadow_policy])
    else:
        parts = [subject]
        if mood:
            parts.append(mood)
    if has_reference:
        parts.append(STYLEREF_PROMPT)
    parts.append(OPAQUE_BG_PROMPT if material == "opaque" else TRANSLUCENT_BG_PROMPT)
    negatives = [p.negative for p in packs if p.negative]
    if brief.negative:
        negatives.append(brief.negative)
    return Prompts(positive=", ".join(p for p in parts if p), negative=", ".join(negatives))


def _position_xy(
    pos: str, canvas_w: int, canvas_h: int, w: int, h: int, clear: int
) -> tuple[int, int]:
    cx = (canvas_w - w) // 2
    cy = (canvas_h - h) // 2
    return {
        "auto": (cx, cy),
        "center": (cx, cy),
        "left": (clear, cy),
        "right": (canvas_w - w - clear, cy),
        "top": (cx, clear),
        "bottom": (cx, canvas_h - h - clear),
    }[pos]


def _fit_scale(scale: float, min_scale: float, asset_id: str) -> float:
    if scale < min_scale:
        raise OrchestrationError(
            f"자산 {asset_id!r}이 캔버스에 맞으려면 scale={scale:.3f}가 필요한데 "
            f"min_scale={min_scale} 미만입니다 — 캔버스를 키우거나 자산 사용 규칙을 확인하세요"
        )
    return scale


def _prepare_asset_layers(
    brief: BriefInput, library: AssetLibrary, canvas: SizeSpec
) -> list[AssetLayer]:
    entries = []
    for ref in brief.assets:
        asset, variant, path = library.resolve(ref)
        entries.append((asset, variant, path, Image.open(path).convert("RGBA")))

    pos = brief.placement_hint.asset_position if brief.placement_hint else "auto"
    layers: list[AssetLayer] = []

    if len(entries) == 1:
        asset, variant, path, img = entries[0]
        clear = asset.usage.clear_space_px
        avail_w, avail_h = canvas.width - 2 * clear, canvas.height - 2 * clear
        if avail_w <= 0 or avail_h <= 0:
            raise OrchestrationError(f"clear_space({clear}px)가 캔버스보다 큽니다")
        scale = _fit_scale(
            min(1.0, avail_w / img.width, avail_h / img.height), asset.usage.min_scale, asset.id
        )
        rgba = scale_asset(img, scale)
        h, w = rgba.shape[:2]
        x, y = _position_xy(pos, canvas.width, canvas.height, w, h, clear)
        layers.append(_layer(asset.id, variant.id, path, rgba, x, y, scale))
        return layers

    # 다중 자산: 가로 일렬 중앙 배치, 균일 스케일 (placement_hint는 무시)
    clear = max(a.usage.clear_space_px for a, _, _, _ in entries)
    gap = max(ASSET_ROW_GAP_PX, clear)
    total_w = sum(img.width for _, _, _, img in entries) + gap * (len(entries) - 1)
    max_h = max(img.height for _, _, _, img in entries)
    scale = min(
        1.0, (canvas.width - 2 * clear) / total_w, (canvas.height - 2 * clear) / max_h
    )
    for asset, _, _, _ in entries:
        _fit_scale(scale, asset.usage.min_scale, asset.id)
    scaled = [
        (asset, variant, path, scale_asset(img, scale))
        for asset, variant, path, img in entries
    ]
    row_w = sum(r.shape[1] for _, _, _, r in scaled) + round(gap * scale) * (len(scaled) - 1)
    cursor = (canvas.width - row_w) // 2
    for asset, variant, path, rgba in scaled:
        h, w = rgba.shape[:2]
        y = (canvas.height - h) // 2
        layers.append(_layer(asset.id, variant.id, path, rgba, cursor, y, scale))
        cursor += w + round(gap * scale)
    return layers


def _layer(
    asset_id: str, variant_id: str, path: Path, rgba: np.ndarray, x: int, y: int, scale: float
) -> AssetLayer:
    return AssetLayer(
        resolved=ResolvedAsset(
            asset_id=asset_id,
            variant_id=variant_id,
            file=str(path),
            file_sha256=file_sha256(path),
            placement=Placement(x=x, y=y, scale=scale),
        ),
        rgba=rgba,
    )


def _build_inpaint_inputs(
    size: SizeSpec, layers: list[AssetLayer]
) -> tuple[Image.Image, Image.Image]:
    """선배치 캔버스(RGB) + 2단 마스크(L). 다중 자산은 마스크 최솟값 병합(모든 코어 보호)."""
    canvas = Image.new("RGBA", (size.width, size.height), (*FORCED_BG_COLOR, 255))
    mask_total = np.full((size.height, size.width), 255, dtype=np.uint8)
    for layer in layers:
        canvas = paste_back(canvas, layer.rgba, layer.resolved.placement)
        mask = np.asarray(
            make_inpaint_mask((size.width, size.height), layer.rgba, layer.resolved.placement)
        )
        mask_total = np.minimum(mask_total, mask)
    return canvas.convert("RGB"), Image.fromarray(mask_total, "L")


@dataclass
class GraphVariant:
    """실행 가능한 워크플로우 한 벌 — 그래프(__meta__ 유지)·버전 핀·모델 매니페스트."""

    workflow_ref: WorkflowRef
    base_graph: dict
    manifest: ModelManifest


@dataclass
class BuildResult:
    job_id: str
    brief: BriefInput
    packs: list[StylePack]
    material: MaterialClass
    chain: list[MattingStrategyId]
    primary: GraphVariant
    registry: MattingNodeRegistry
    seeds: list[int]
    prompts: Prompts
    size: SizeSpec
    postprocess_spec: PostprocessSpec
    # native_alpha 빌드에서 trimap 폴백이 쓸 일반 gen 그래프 (모델·워크플로우가 다르다)
    fallback: GraphVariant | None = None
    assets: list[AssetLayer] = field(default_factory=list)
    input_images: dict[str, str] = field(default_factory=dict)
    skip_matting: bool = False
    backend_group: str | None = None  # 원격 실행에서 잡을 받을 모델 계열 서비스

    def _variant_for(self, strategy: MattingStrategyId | None) -> GraphVariant:
        needs_fragment = strategy is not None and STRATEGY_FRAGMENTS[strategy] is not None
        if needs_fragment and self.primary.workflow_ref.id in NATIVE_ALPHA_WORKFLOWS:
            if self.fallback is None:
                raise MattingNodeNotConfigured(
                    f"{strategy!r} 폴백에 쓸 일반 생성 그래프가 없습니다 — "
                    "모델 프로파일과 함께 빌드해야 폴백 체인이 완성됩니다"
                )
            return self.fallback
        return self.primary

    def graph_for(self, strategy: MattingStrategyId | None) -> dict:
        variant = self._variant_for(strategy)
        if strategy is None:
            return strip_meta(variant.base_graph)
        fragment_name = STRATEGY_FRAGMENTS[strategy]
        if fragment_name is None:
            # native_alpha: 후처리 조각이 아니라 워크플로우 자체가 알파를 산출해야 한다
            if variant.workflow_ref.id in NATIVE_ALPHA_WORKFLOWS:
                return strip_meta(variant.base_graph)
            raise MattingNodeNotConfigured(
                f"{strategy!r}는 네이티브 알파 워크플로우가 필요합니다 "
                f"(현재: {variant.workflow_ref.id!r}, 지원: {sorted(NATIVE_ALPHA_WORKFLOWS)}) — "
                "모델 프로파일 또는 스타일 팩의 워크플로우를 확인하세요"
            )
        config = self.registry.get(strategy)
        graph = strip_meta(compose(variant.base_graph, load_fragment(fragment_name)))
        return fill_slots(graph, config.slots, allow_missing=True)

    def payload_for(
        self, strategy: MattingStrategyId | None, seeds: list[int]
    ) -> JobPayload:
        variant = self._variant_for(strategy)
        graph = self.graph_for(strategy)
        leftover = unfilled_slots(graph)
        allowed = {f"__{name.upper()}__" for name in self.input_images}
        unexpected = leftover - allowed
        if unexpected:
            raise OrchestrationError(f"미해결 슬롯: {sorted(unexpected)}")
        # Layered류는 [컴포지트, 레이어...]를 출력 — 마지막 장이 투명 오브젝트 레이어
        output_index = -1 if variant.workflow_ref.id in NATIVE_ALPHA_WORKFLOWS else 0
        return JobPayload(
            job_id=self.job_id,
            workflow=variant.workflow_ref,
            graph=graph,
            model_manifest=variant.manifest,
            style_packs=[StylePackRef(id=p.id, version=p.version) for p in self.packs],
            matting_chain=self.chain,
            seeds=seeds,
            size=self.size,
            prompts=self.prompts,
            assets=[layer.resolved for layer in self.assets],
            input_images=self.input_images,
            output_index=output_index,
            backend_group=self.backend_group,
        )


def build_job(
    brief: BriefInput,
    packs_by_id: dict[str, StylePack],
    library: AssetLibrary | None = None,
    registry: MattingNodeRegistry | None = None,
    profiles: ModelProfileRegistry | None = None,
    job_id: str | None = None,
    workdir: Path | None = None,
    skip_matting: bool = False,
    engine_flavor: EngineFlavor = "local",
) -> BuildResult:
    job_id = job_id or uuid.uuid4().hex[:12]
    registry = registry or MattingNodeRegistry()
    try:
        packs = [packs_by_id[pack_id] for pack_id in brief.style.style_packs]
    except KeyError as exc:
        raise OrchestrationError(f"알 수 없는 스타일 팩: {exc.args[0]!r}") from exc
    if not packs and profiles is None:
        # 레거시(프로파일 없음)는 팩이 모델·워크플로우를 공급하므로 필수
        raise OrchestrationError("스타일 팩이 최소 1개 필요합니다")
    primary = packs[0] if packs else None
    material = primary.material_class if primary else "opaque"
    size = brief.size()
    refs = list(brief.reference_images)
    prompts = build_prompts(brief, packs, material, has_reference=bool(refs))
    chain = chain_for(material)

    # ── 워크플로우·모델 결정: 프로파일(사용자 모델 선택) 우선, 없으면 팩 레거시 폴백 ──
    profile = None
    if profiles is not None:
        try:
            profile = profiles.get(brief.model)
        except KeyError as exc:
            raise OrchestrationError(str(exc)) from exc
    elif brief.model:
        raise OrchestrationError("모델 선택(brief.model)에는 프로파일 레지스트리가 필요합니다")

    remote = None
    if engine_flavor == "remote":
        if profile is None:
            raise OrchestrationError("원격 백엔드 실행에는 모델 프로파일 레지스트리가 필요합니다")
        if profile.remote is None:
            raise OrchestrationError(
                f"{profile.id!r} 프로파일은 원격 백엔드를 지원하지 않습니다 "
                "(model_profiles.yaml의 remote 블록 부재)"
            )
        remote = profile.remote

    # /api/models와 같은 판정을 쓴다 — UI 표시와 여기서의 실패가 어긋나지 않게.
    capability = model_capability(profile, engine_flavor) if profile is not None else None

    # 네이티브 알파 미지원 프로파일(FLUX.2 등)·원격 백엔드의 translucent는 trimap으로 바로 강등
    if capability is not None and not capability.supports_native_alpha:
        chain = [s for s in chain if s != "native_alpha"]
        if not chain:
            raise OrchestrationError(f"{profile.id!r} 프로파일로 실행 가능한 배경 제거 전략이 없습니다")

    fallback_plan: tuple[str, ModelManifest, SamplingSpec | None] | None = None
    if profile:
        if brief.assets and refs:
            raise OrchestrationError("자산 인페인트와 참조 이미지 동시 사용은 아직 지원하지 않습니다 (v1)")
        sampling = (primary.sampling if primary else None) or profile.sampling
        if refs:
            if material == "translucent":
                raise OrchestrationError("참조 이미지는 translucent 팩과 동시 사용을 지원하지 않습니다 (v1)")
            # 판정은 capability 하나로 하고, 분기는 사용자에게 보일 문구를 고르는 데만 쓴다.
            if capability is not None and not capability.supports_styleref:
                if not profile.workflow_styleref:
                    raise OrchestrationError(
                        f"{profile.id!r} 프로파일은 참조 이미지를 지원하지 않습니다 — qwen-image를 사용하세요"
                    )
                raise OrchestrationError(
                    f"{profile.id!r} 프로파일의 원격 백엔드는 참조 이미지를 지원하지 않습니다 "
                    "— 로컬 엔진(PM_ENGINE 미설정)으로 실행하세요"
                )
            workflow_id = profile.workflow_styleref
            manifest = profile.styleref_manifest or profile.manifest
        elif brief.assets:
            if not profile.workflow_inpaint:
                raise OrchestrationError(f"{profile.id!r} 프로파일은 자산 인페인팅을 지원하지 않습니다")
            workflow_id = profile.workflow_inpaint
            manifest = profile.manifest
        elif material == "translucent" and profile.native_alpha and chain[0] == "native_alpha":
            native = profile.native_alpha
            workflow_id = native.workflow
            manifest = native.manifest
            sampling = (primary.sampling if primary else None) or native.sampling or profile.sampling
            # trimap 폴백은 일반 gen 워크플로우·모델로 돌아간다
            fallback_plan = (profile.workflow_gen, profile.manifest, profile.sampling)
        else:
            workflow_id = profile.workflow_gen
            manifest = profile.manifest
    else:
        if refs:
            raise OrchestrationError("참조 이미지 경로는 모델 프로파일 레지스트리가 필요합니다")
        sampling = primary.sampling
        manifest = primary.model_manifest
        if brief.assets:
            workflow_id = INPAINT_VARIANTS.get(primary.workflow_template)
            if workflow_id is None:
                raise OrchestrationError(
                    f"{primary.workflow_template!r}에 대응하는 인페인팅 워크플로우가 없습니다 "
                    f"(지원: {sorted(INPAINT_VARIANTS)})"
                )
        else:
            workflow_id = primary.workflow_template

    # ── 원격 파일 셋 적용 (원격 GPU에 맞춘 양자화 변형 등) ──
    if remote is not None:
        try:
            manifest = remote.apply(manifest)
            if fallback_plan is not None:
                fb_workflow, fb_manifest, fb_sampling = fallback_plan
                fallback_plan = (fb_workflow, remote.apply(fb_manifest), fb_sampling)
        except ValueError as exc:
            raise OrchestrationError(str(exc)) from exc

    # ── 입력 이미지 준비 (자산 선배치 / 참조 이미지 사본) ──
    assets: list[AssetLayer] = []
    input_images: dict[str, str] = {}
    if brief.assets:
        if library is None:
            raise OrchestrationError("자산 참조가 있는데 자산 라이브러리가 없습니다")
        if workdir is None:
            raise OrchestrationError("자산 경로에는 workdir가 필요합니다 (선배치 캔버스·마스크 저장)")
        assets = _prepare_asset_layers(brief, library, size)
        canvas_img, mask_img = _build_inpaint_inputs(size, assets)
        workdir.mkdir(parents=True, exist_ok=True)
        canvas_img.save(workdir / "canvas.png")
        mask_img.save(workdir / "mask.png")
        input_images = {
            "canvas": str(workdir / "canvas.png"),
            "mask_image": str(workdir / "mask.png"),
        }
    if refs:
        if workdir is None:
            raise OrchestrationError("참조 이미지 경로에는 workdir가 필요합니다 (재현용 사본 저장)")
        workdir.mkdir(parents=True, exist_ok=True)
        for index, ref in enumerate(refs, start=1):
            source = Path(ref)
            if not source.is_file():
                raise OrchestrationError(f"참조 이미지가 없습니다: {ref}")
            copy_path = workdir / f"ref{index}.png"
            Image.open(source).convert("RGB").save(copy_path)
            input_images[f"ref{index}"] = str(copy_path)

    primary_variant = _make_variant(workflow_id, manifest, sampling, prompts, size, job_id)
    if refs and len(refs) >= 2:
        _attach_second_reference(primary_variant.base_graph)

    fallback_variant = None
    if fallback_plan is not None:
        fb_workflow, fb_manifest, fb_sampling = fallback_plan
        fallback_variant = _make_variant(fb_workflow, fb_manifest, fb_sampling, prompts, size, job_id)

    spec = PostprocessSpec() if material == "opaque" else PostprocessSpec(defringe_bg=None)
    return BuildResult(
        job_id=job_id,
        brief=brief,
        packs=packs,
        material=material,
        chain=chain,
        primary=primary_variant,
        fallback=fallback_variant,
        registry=registry,
        seeds=resolve_seeds(brief),
        prompts=prompts,
        size=size,
        postprocess_spec=spec,
        assets=assets,
        input_images=input_images,
        skip_matting=skip_matting,
        backend_group=remote.backend_group if remote else None,
    )


def _make_variant(
    workflow_id: str,
    manifest: ModelManifest,
    sampling: SamplingSpec | None,
    prompts: Prompts,
    size: SizeSpec,
    job_id: str,
) -> GraphVariant:
    template, workflow_ref = load_template(workflow_id)
    model_file = next(
        (f.name for f in manifest.files if f.kind in ("checkpoint", "unet")),
        manifest.model_id,  # 핀 전 placeholder — 실행 시 ComfyUI가 거부한다
    )
    slots: dict[str, object] = {
        "model": model_file,
        "positive": prompts.positive,
        "negative": prompts.negative,
        "width": size.width,
        "height": size.height,
        "steps": sampling.steps if sampling else DEFAULT_STEPS,
        "cfg": sampling.cfg if sampling else DEFAULT_CFG,
        "prefix": f"pm_{job_id}",
        "denoise": DEFAULT_INPAINT_DENOISE,
    }
    # 분리 로더 워크플로우용 — 매니페스트에 있으면 슬롯 제공, 템플릿이 안 쓰면 무시된다
    for kind in ("clip", "vae"):
        file = next((f.name for f in manifest.files if f.kind == kind), None)
        if file:
            slots[kind] = file
    return GraphVariant(
        workflow_ref=workflow_ref,
        base_graph=fill_slots(template, slots, allow_missing=True),
        manifest=manifest,
    )


def _attach_second_reference(graph: dict) -> None:
    """styleref 그래프에 두 번째 참조 이미지를 동적 배선한다 (__meta__.styleref 규약)."""
    meta = graph.get(META_KEY, {})
    styleref = meta.get("styleref")
    if not styleref:
        raise OrchestrationError("이 워크플로우는 참조 이미지 2장을 지원하지 않습니다 (__meta__.styleref 부재)")
    encode_node = styleref["encode_node"]
    graph["ref2_load"] = {"class_type": "LoadImage", "inputs": {"image": "__REF2__"}}
    graph[encode_node]["inputs"]["image2"] = ["ref2_load", 0]


@dataclass
class CandidateOutcome:
    index: int
    seed: int
    final_path: Path | None
    qa: CandidateQA


@dataclass
class JobRunResult:
    job_id: str
    job_dir: Path
    strategy: MattingStrategyId | None
    report: QAReport
    candidates: list[CandidateOutcome]


# 진행 표시(큐 대기→엔진 준비 중→생성 n/k)용 — (state, done, total)
ProgressFn = Callable[[str, int, int], None]


CancelCheck = Callable[[], bool]


def _run_engine(
    engine: WorkflowEngine,
    payload: JobPayload,
    dest: Path,
    poll_interval: float,
    timeout_s: float,
    report: ProgressFn,
    cancel_check: CancelCheck | None = None,
) -> list[CandidateResult]:
    def _cancelled() -> bool:
        return bool(cancel_check and cancel_check())

    if _cancelled():
        raise JobCancelledError(f"제출 전 취소됨: {payload.job_id}")
    report("preparing", 0, len(payload.seeds))
    handle = engine.submit(payload)
    deadline = time.time() + timeout_s
    while True:
        if _cancelled():
            cancel = getattr(engine, "cancel", None)
            if cancel:
                cancel(handle)
            raise JobCancelledError(f"사용자 취소: {payload.job_id}")
        status = engine.poll(handle)
        if status.state == "failed":
            raise JobFailedError(status.error or "엔진이 원인 없이 실패를 보고했습니다")
        report("running", status.done_count, status.total)
        if status.state == "done":
            break
        if time.time() > deadline:
            raise JobTimeoutError(f"생성 대기 초과({timeout_s}s): job={payload.job_id}")
        time.sleep(poll_interval)
    return engine.fetch(handle, dest)


def _process_candidate(
    result: CandidateResult,
    build: BuildResult,
    thresholds: QAThresholds,
    job_dir: Path,
    index_override: int | None = None,
) -> CandidateOutcome:
    index = index_override if index_override is not None else result.index
    img = Image.open(result.path)
    raw_rgba = np.asarray(img.convert("RGBA"))
    canvas_alpha = raw_rgba[..., 3].copy()

    # 배경색은 고정값 대신 원본 코너에서 추정 — 표류하는 실제 배경색에 맞춰 헤일로 오탐 방지
    bg_estimate = estimate_bg_color(raw_rgba)
    spec = build.postprocess_spec
    if spec.defringe_bg is not None and bg_estimate is not None:
        spec = replace(spec, defringe_bg=bg_estimate)

    checks: list[QACheck] = []
    final: Image.Image | None = None
    offset = (0, 0)
    try:
        final, offset = postprocess(img, spec)
    except ForegroundNotDetected as exc:
        checks.append(
            QACheck(
                rule=RULE_FOREGROUND,
                passed=False,
                detail=str(exc),
                regen_hint="전경이 감지되지 않았습니다 — 프롬프트·배경 제거 전략을 점검하고 재생성하세요",
            )
        )

    final_path: Path | None = None
    if final is not None:
        dx, dy = offset
        for layer in build.assets:
            base = layer.resolved.placement
            try:
                adjusted = Placement(x=base.x + dx, y=base.y + dy, scale=base.scale)
                final = paste_back(final, layer.rgba, adjusted)
                ok = verify_core_hash(final, layer.rgba, adjusted)
            except (PlacementOutOfBounds, ValidationError) as exc:
                checks.append(
                    QACheck(
                        rule=RULE_CORE_HASH,
                        passed=False,
                        detail=f"{layer.resolved.asset_id}: 배치 보정 실패 — {exc}",
                        regen_hint="자산 배치가 크롭 결과를 벗어났습니다 — 재생성하세요",
                    )
                )
                continue
            checks.append(
                QACheck(
                    rule=RULE_CORE_HASH,
                    passed=ok,
                    detail=f"{layer.resolved.asset_id}:{layer.resolved.variant_id}",
                    regen_hint=None if ok else "자산 코어 해시 불일치 — 파생 시드 재시도 대상",
                )
            )
        checks.extend(
            run_qa(
                final,
                canvas_alpha,
                build.material,
                thresholds,
                bg_color=bg_estimate or FORCED_BG_COLOR,
            )
        )
        final_dir = job_dir / "final"
        final_dir.mkdir(parents=True, exist_ok=True)
        final_path = final_dir / f"{index:02d}_{result.seed}.png"
        final.save(final_path)

    return CandidateOutcome(
        index=index,
        seed=result.seed,
        final_path=final_path,
        qa=CandidateQA(index=index, seed=result.seed, checks=checks),
    )


def _foreground_missing(outcome: CandidateOutcome) -> bool:
    return any(c.rule == RULE_FOREGROUND and not c.passed for c in outcome.qa.checks)


def _hash_failed(outcome: CandidateOutcome) -> bool:
    return any(c.rule == RULE_CORE_HASH and not c.passed for c in outcome.qa.checks)


def execute_job(
    build: BuildResult,
    engine: WorkflowEngine,
    config: Config,
    thresholds: QAThresholds | None = None,
    poll_interval: float = 2.0,
    timeout_s: float = 600.0,
    retry_hash_failures: bool = True,
    progress: ProgressFn | None = None,
    cancel_check: CancelCheck | None = None,
) -> JobRunResult:
    def report(state: str, done: int = 0, total: int = 0) -> None:
        if progress:
            progress(state, done, total)

    thresholds = thresholds or QAThresholds()
    job_dir = config.storage / "jobs" / build.job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    strategies: list[MattingStrategyId | None] = (
        [None] if build.skip_matting else list(build.chain)
    )
    outcomes: list[CandidateOutcome] | None = None
    used_strategy: MattingStrategyId | None = None
    last_error: str | None = None

    for strategy in strategies:
        payload = build.payload_for(strategy, build.seeds)
        (job_dir / "payload.json").write_text(
            payload.model_dump_json(indent=2), encoding="utf-8"
        )
        try:
            results = _run_engine(
                engine,
                payload,
                job_dir / "candidates",
                poll_interval,
                timeout_s,
                report,
                cancel_check,
            )
        except JobFailedError as exc:
            last_error = str(exc)
            continue
        report("postprocess", len(results), len(results))
        attempt = [_process_candidate(r, build, thresholds, job_dir) for r in results]
        if all(_foreground_missing(o) for o in attempt):
            last_error = f"전 후보 전경 미검출 (strategy={strategy})"
            continue
        outcomes = attempt
        used_strategy = strategy
        break

    if outcomes is None:
        raise OrchestrationError(
            f"배경 제거 폴백 체인 소진 — 재생성이 필요합니다 (마지막 오류: {last_error})"
        )

    if retry_hash_failures and build.assets:
        failed = [o for o in outcomes if _hash_failed(o)]
        if failed:
            retry_seeds = [(o.seed + RETRY_SEED_OFFSET) % SEED_SPACE for o in failed]
            retry_payload = build.payload_for(used_strategy, retry_seeds)
            retry_results = _run_engine(
                engine,
                retry_payload,
                job_dir / "candidates-retry",
                poll_interval,
                timeout_s,
                report,
                cancel_check,
            )
            for original, retried in zip(failed, retry_results):
                outcomes[original.index] = _process_candidate(
                    retried, build, thresholds, job_dir, index_override=original.index
                )

    report = QAReport(
        job_id=build.job_id,
        material_class=build.material,
        candidates=[o.qa for o in sorted(outcomes, key=lambda o: o.index)],
    )
    (job_dir / "qa_report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return JobRunResult(
        job_id=build.job_id,
        job_dir=job_dir,
        strategy=used_strategy,
        report=report,
        candidates=outcomes,
    )
