from pathlib import Path

import numpy as np
import pytest
import yaml
from PIL import Image

from piemgmaker.assets_lib.library import AssetLibrary
from piemgmaker.config import load_config
from piemgmaker.engine.contract import CandidateResult, JobHandle, JobStatus
from piemgmaker.pipeline.matting_router import (
    MattingNodeConfig,
    MattingNodeNotConfigured,
    MattingNodeRegistry,
)
from piemgmaker.pipeline.orchestrate import (
    OrchestrationError,
    build_job,
    build_prompts,
    execute_job,
    resolve_seeds,
)
from piemgmaker.schemas.brief import BriefInput
from piemgmaker.schemas.style_pack import StylePack
from tests.conftest import make_rgba

BRIEF_BASE = {
    "campaign_text": "여름 세일",
    "object_concept": "글로시 쇼핑백",
    "style": {"style_packs": ["test-pack"]},
    "size_preset": {"width": 512, "height": 512},
    "candidate_count": 2,
    "seed": 7,
}


def make_pack(**overrides) -> StylePack:
    data = {
        "id": "test-pack",
        "version": "0.1.0",
        "prompt_template": "{subject}, test style, {mood}",
        "negative": "text",
        "material_class": "opaque",
        "shadow_policy": "soft_floor",
        "workflow_template": "object-gen-v1",
        "model_manifest": {"model_id": "m"},
        "matting": {"strategy": "segment", "model": "m"},
    }
    data.update(overrides)
    return StylePack.model_validate(data)


class FakeEngine:
    """생성 구간 대체 — 결정론 후단(후처리·paste-back·QA)을 실 데이터로 검증한다."""

    name = "fake"

    def __init__(self, factory):
        self._factory = factory
        self.payloads = []

    def submit(self, payload):
        self.payloads.append(payload)
        return JobHandle(
            engine=self.name,
            job_id=payload.job_id,
            seeds=payload.seeds,
            remote_ids=[f"r{i}" for i in range(len(payload.seeds))],
        )

    def poll(self, handle):
        return JobStatus(state="done", done_count=len(handle.seeds), total=len(handle.seeds))

    def fetch(self, handle, dest: Path):
        dest.mkdir(parents=True, exist_ok=True)
        results = []
        for i, seed in enumerate(handle.seeds):
            path = dest / f"{i:02d}_{seed}.png"
            self._factory(seed).save(path)
            results.append(CandidateResult(index=i, seed=seed, path=path))
        return results


def blob_512() -> Image.Image:
    """매팅 완료를 가정한 후보: 512 캔버스 중앙 312x312 불투명 블롭 (QA 전 룰 통과 형상)."""
    arr = make_rgba(512, 512)
    arr[100:412, 100:412] = (200, 30, 30, 255)
    return Image.fromarray(arr, "RGBA")


def test_시드는_마스터에서_연속_파생되고_카드별로_기록_가능하다():
    brief = BriefInput.model_validate(BRIEF_BASE)
    assert resolve_seeds(brief) == [7, 8]

    random_brief = BriefInput.model_validate({**BRIEF_BASE, "seed": "random"})
    seeds = resolve_seeds(random_brief)
    assert len(seeds) == 2 and all(0 <= s < 2**63 for s in seeds)


def test_프롬프트는_팩_템플릿_그림자_배경지시_네거티브를_병합한다():
    brief = BriefInput.model_validate({**BRIEF_BASE, "negative": "hands"})
    prompts = build_prompts(brief, [make_pack()], "opaque")
    assert "글로시 쇼핑백" in prompts.positive
    assert "soft floor contact shadow" in prompts.positive
    assert "neutral gray background" in prompts.positive
    assert prompts.negative == "text, hands"


def test_알_수_없는_팩과_팩_없는_브리프는_거부한다():
    brief = BriefInput.model_validate(BRIEF_BASE)
    with pytest.raises(OrchestrationError):
        build_job(brief, {})

    no_style = BriefInput.model_validate(
        {k: v for k, v in BRIEF_BASE.items() if k != "style"}
    )
    with pytest.raises(OrchestrationError):
        build_job(no_style, {"test-pack": make_pack()})


def test_실행은_후보별_최종_PNG와_QA_리포트를_남기고_버전을_핀한다(tmp_path):
    config = load_config(env={"PM_STORAGE": str(tmp_path / "out")})
    brief = BriefInput.model_validate(BRIEF_BASE)
    build = build_job(brief, {"test-pack": make_pack()}, job_id="j1", skip_matting=True)
    engine = FakeEngine(lambda seed: blob_512())

    result = execute_job(build, engine, config)

    assert result.report.passed
    assert (result.job_dir / "payload.json").is_file()
    assert (result.job_dir / "qa_report.json").is_file()
    assert [c.final_path.name for c in result.candidates] == ["00_7.png", "01_8.png"]
    payload = engine.payloads[0]
    assert payload.workflow.id == "object-gen-v1" and len(payload.workflow.hash) == 64
    assert [ref.id for ref in payload.style_packs] == ["test-pack"]


def test_자산_경로는_선배치_입력을_만들고_코어_해시가_전_후보_통과한다(tmp_path):
    lib_root = tmp_path / "assets"
    (lib_root / "badge").mkdir(parents=True)
    Image.fromarray(make_rgba(64, 64, (20, 40, 200, 255)), "RGBA").save(
        lib_root / "badge" / "main.png"
    )
    (lib_root / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "assets": [
                    {
                        "id": "badge",
                        "name": "배지",
                        "type": "object",
                        "variants": [{"id": "main", "file": "badge/main.png"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    config = load_config(env={"PM_STORAGE": str(tmp_path / "out")})
    brief = BriefInput.model_validate({**BRIEF_BASE, "assets": ["badge"]})
    workdir = tmp_path / "out" / "jobs" / "j2" / "inputs"
    build = build_job(
        brief,
        {"test-pack": make_pack()},
        library=AssetLibrary(lib_root),
        job_id="j2",
        workdir=workdir,
        skip_matting=True,
    )
    assert (workdir / "canvas.png").is_file()
    assert (workdir / "mask.png").is_file()
    assert build.payload_for(None, build.seeds).protected_asset

    result = execute_job(build, FakeEngine(lambda seed: blob_512()), config)

    core_checks = [
        check
        for candidate in result.report.candidates
        for check in candidate.checks
        if check.rule == "core-hash"
    ]
    assert core_checks and all(check.passed for check in core_checks)
    assert result.report.passed


def _asset_library(tmp_path) -> AssetLibrary:
    lib_root = tmp_path / "assets"
    (lib_root / "badge").mkdir(parents=True)
    Image.fromarray(make_rgba(64, 64, (20, 40, 200, 255)), "RGBA").save(
        lib_root / "badge" / "main.png"
    )
    (lib_root / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "assets": [
                    {
                        "id": "badge",
                        "name": "배지",
                        "type": "object",
                        "variants": [{"id": "main", "file": "badge/main.png"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return AssetLibrary(lib_root)


def test_blend_팩은_fidelity_룰로_기록되고_통과한다(tmp_path):
    config = load_config(env={"PM_STORAGE": str(tmp_path / "out")})
    brief = BriefInput.model_validate({**BRIEF_BASE, "assets": ["badge"]})
    pack = make_pack(blend={"strength": 0.6})
    build = build_job(
        brief,
        {"test-pack": pack},
        library=_asset_library(tmp_path),
        job_id="j3",
        workdir=tmp_path / "out" / "jobs" / "j3" / "inputs",
        skip_matting=True,
    )
    assert "pre-placed graphic" in build.prompts.positive  # blend 활성 인페인트 프롬프트

    result = execute_job(build, FakeEngine(lambda seed: blob_512()), config)

    fidelity_checks = [
        check
        for candidate in result.report.candidates
        for check in candidate.checks
        if check.rule == "asset-fidelity"
    ]
    assert fidelity_checks and all(check.passed for check in fidelity_checks)
    # blend 경로에서는 core-hash 룰이 기록되지 않는다 (계약 교체)
    assert not any(
        check.rule == "core-hash"
        for candidate in result.report.candidates
        for check in candidate.checks
    )
    assert result.report.passed


def test_기하_변환은_placement에_기록되고_실행이_통과한다(tmp_path):
    config = load_config(env={"PM_STORAGE": str(tmp_path / "out")})
    brief = BriefInput.model_validate({**BRIEF_BASE, "assets": ["badge"]})
    pack = make_pack(
        blend={"strength": 0.5, "geometry": {"rotation_deg": 15, "tilt": "slight"}}
    )
    build = build_job(
        brief,
        {"test-pack": pack},
        library=_asset_library(tmp_path),
        job_id="j4",
        workdir=tmp_path / "out" / "jobs" / "j4" / "inputs",
        skip_matting=True,
    )
    placement = build.assets[0].resolved.placement
    assert placement.rotation_deg == 15
    assert placement.perspective is not None

    result = execute_job(build, FakeEngine(lambda seed: blob_512()), config)
    assert result.report.passed


def test_core_noise는_인페인트_마스크_코어를_저강도로_연다(tmp_path):
    brief = BriefInput.model_validate({**BRIEF_BASE, "assets": ["badge"]})
    pack = make_pack(
        blend={"strength": 0.6, "generative": {"core_noise": 40, "band_px": 12, "ramp": "smoothstep"}}
    )
    workdir = tmp_path / "inputs"
    build = build_job(
        brief,
        {"test-pack": pack},
        library=_asset_library(tmp_path),
        job_id="j5",
        workdir=workdir,
        skip_matting=True,
    )
    mask = np.asarray(Image.open(workdir / "mask.png"))
    assert mask.min() == 40  # 코어가 0이 아니라 저강도 개방
    assert mask.max() == 255
    assert build.blend is not None and build.blend.generative.core_noise == 40


def test_blend_없는_팩은_기존_core_hash_경로_그대로다(tmp_path):
    config = load_config(env={"PM_STORAGE": str(tmp_path / "out")})
    brief = BriefInput.model_validate({**BRIEF_BASE, "assets": ["badge"]})
    build = build_job(
        brief,
        {"test-pack": make_pack()},
        library=_asset_library(tmp_path),
        job_id="j6",
        workdir=tmp_path / "out" / "jobs" / "j6" / "inputs",
        skip_matting=True,
    )
    assert build.blend is None
    assert "pre-placed graphic" not in build.prompts.positive
    mask = np.asarray(Image.open(tmp_path / "out" / "jobs" / "j6" / "inputs" / "mask.png"))
    assert mask.min() == 0  # 코어 완전 보호 유지

    result = execute_job(build, FakeEngine(lambda seed: blob_512()), config)
    rules = {
        check.rule
        for candidate in result.report.candidates
        for check in candidate.checks
    }
    assert "core-hash" in rules and "asset-fidelity" not in rules
    assert result.report.passed


def _qwen_profiles():
    from piemgmaker.schemas.model_profile import ModelProfileRegistry

    return ModelProfileRegistry.model_validate(
        {
            "default": "qwen-image",
            "profiles": [
                {
                    "id": "qwen-image",
                    "label": "Qwen",
                    "workflow_gen": "object-gen-qwen-v1",
                    "workflow_inpaint": "object-inpaint-qwen-v1",
                    "workflow_styleref": "object-gen-styleref-v1",
                    "workflow_logoref": "object-gen-logoref-v1",
                    "sampling": {"steps": 20, "cfg": 2.5},
                    "manifest": {
                        "model_id": "qwen-base",
                        "files": [{"name": "qwen.gguf", "kind": "unet"}],
                    },
                    "styleref_manifest": {
                        "model_id": "qwen-edit",
                        "files": [
                            {"name": "qwen-edit.gguf", "kind": "unet"},
                            {"name": "clip.safetensors", "kind": "clip"},
                            {"name": "vae.safetensors", "kind": "vae"},
                        ],
                    },
                },
                {
                    "id": "no-logoref",
                    "label": "NoLogoref",
                    "workflow_gen": "object-gen-qwen-v1",
                    "sampling": {"steps": 20, "cfg": 2.5},
                    "manifest": {"model_id": "m", "files": []},
                },
            ],
        }
    )


def test_logo_reference는_Edit_스택_logoref_워크플로우로_빌드된다(tmp_path):
    brief = BriefInput.model_validate({**BRIEF_BASE, "logo_reference": "badge"})
    workdir = tmp_path / "inputs"
    build = build_job(
        brief,
        {"test-pack": make_pack()},
        library=_asset_library(tmp_path),
        profiles=_qwen_profiles(),
        job_id="j7",
        workdir=workdir,
        skip_matting=True,
    )
    payload = build.payload_for(None, build.seeds)
    assert payload.workflow.id == "object-gen-logoref-v1"
    assert payload.model_manifest.model_id == "qwen-edit"  # styleref와 같은 Edit 스택
    assert "reproduce the reference logo faithfully" in payload.prompts.positive
    assert payload.logo_reference == "badge"
    assert payload.protected_asset  # 로고 참조도 브랜드 자산 보호 대상
    # 알파 로고는 흰 배경 RGB로 변환돼 참조 입력이 된다
    ref = Image.open(workdir / "logoref.png")
    assert ref.mode == "RGB"
    assert payload.input_images["ref1"] == str(workdir / "logoref.png")
    assert not payload.assets  # 생성 트랙 — 합성 레이어 없음


def test_logo_reference는_자산·참조와_동시_사용을_거부한다(tmp_path):
    lib = _asset_library(tmp_path)
    for extra in ({"assets": ["badge"]}, {"reference_images": [str(tmp_path / "r.png")]}):
        brief = BriefInput.model_validate({**BRIEF_BASE, "logo_reference": "badge", **extra})
        with pytest.raises(OrchestrationError):
            build_job(
                brief,
                {"test-pack": make_pack()},
                library=lib,
                profiles=_qwen_profiles(),
                workdir=tmp_path / "w",
            )


def test_logoref_미지원_프로파일은_거부한다(tmp_path):
    brief = BriefInput.model_validate(
        {**BRIEF_BASE, "logo_reference": "badge", "model": "no-logoref"}
    )
    with pytest.raises(OrchestrationError, match="로고 참조 생성을 지원하지 않습니다"):
        build_job(
            brief,
            {"test-pack": make_pack()},
            library=_asset_library(tmp_path),
            profiles=_qwen_profiles(),
            workdir=tmp_path / "w",
        )


def test_asset_blend_mode_오버라이드는_팩_없는_blend도_활성화한다(tmp_path):
    library = _asset_library(tmp_path)
    brief = BriefInput.model_validate(
        {**BRIEF_BASE, "assets": ["badge"], "asset_blend_mode": "imprint"}
    )
    build = build_job(
        brief,
        {"test-pack": make_pack()},  # 팩에 blend 블록 없음
        library=library,
        workdir=tmp_path / "w",
        skip_matting=True,
    )
    assert build.blend is not None and build.blend.mode == "imprint"
    # imprint 오버라이드는 새김 기준 ΔE 상한(≥30)으로 보정된다
    assert build.blend.fidelity.mean_delta_e_max >= 30.0

    overlay = BriefInput.model_validate(
        {**BRIEF_BASE, "assets": ["badge"], "asset_blend_mode": "overlay"}
    )
    build2 = build_job(
        overlay,
        {"test-pack": make_pack(blend={"strength": 0.5, "mode": "imprint"})},
        library=library,
        workdir=tmp_path / "w2",
        skip_matting=True,
    )
    assert build2.blend.mode == "overlay"  # 브리프가 팩 설정을 이긴다


def test_자산_참조가_있는데_라이브러리가_없으면_거부한다():
    brief = BriefInput.model_validate({**BRIEF_BASE, "assets": ["badge"]})
    with pytest.raises(OrchestrationError):
        build_job(brief, {"test-pack": make_pack()})


def test_매팅_노드_미배선은_폴백이_아니라_명시적_오류다(tmp_path):
    config = load_config(env={"PM_STORAGE": str(tmp_path)})
    brief = BriefInput.model_validate(BRIEF_BASE)
    build = build_job(
        brief,
        {
            "test-pack": make_pack(
                material_class="translucent",
                matting={"strategy": "native_alpha", "model": "m"},
            )
        },
    )
    with pytest.raises(MattingNodeNotConfigured):
        execute_job(build, FakeEngine(lambda seed: blob_512()), config)


def test_세그먼트_전략은_기본_레지스트리로_조각이_합성된_그래프를_만든다():
    registry = MattingNodeRegistry(
        {"segment": MattingNodeConfig(slots={"segment_model": "BiRefNet-matting"})}
    )
    brief = BriefInput.model_validate(BRIEF_BASE)
    build = build_job(brief, {"test-pack": make_pack()}, registry=registry)
    payload = build.payload_for("segment", build.seeds)
    node = payload.graph["mat_F1"]
    assert node["class_type"] == "BiRefNetRMBG"
    assert node["inputs"]["model"] == "BiRefNet-matting"
    assert node["inputs"]["image"] == ["7", 0]
    assert payload.graph["8"]["inputs"]["images"] == ["mat_F1", 0]


def test_기본_레지스트리는_segment와_trimap이_배선돼_있다():
    registry = MattingNodeRegistry()
    assert registry.configured("segment")
    assert registry.configured("trimap")
    assert not registry.configured("native_alpha")
