from pathlib import Path

import pytest
from PIL import Image

from piemgmaker.pipeline.orchestrate import OrchestrationError, build_job
from piemgmaker.schemas.brief import BriefInput
from piemgmaker.schemas.model_profile import (
    ModelProfileRegistry,
    load_model_profiles,
)
from tests.conftest import make_rgba
from tests.test_orchestrate import BRIEF_BASE, make_pack

REPO_ROOT = Path(__file__).parent.parent


def make_registry(**overrides) -> ModelProfileRegistry:
    data = {
        "default": "qwen-image",
        "profiles": [
            {
                "id": "qwen-image",
                "label": "Qwen",
                "workflow_gen": "object-gen-qwen-v1",
                "workflow_inpaint": "object-inpaint-qwen-v1",
                "workflow_styleref": "object-gen-styleref-v1",
                "sampling": {"steps": 20, "cfg": 2.5},
                "manifest": {
                    "model_id": "qwen",
                    "files": [
                        {"name": "qwen.gguf", "kind": "unet"},
                        {"name": "qwen_te.safetensors", "kind": "clip"},
                        {"name": "qwen_vae.safetensors", "kind": "vae"},
                    ],
                },
                "styleref_manifest": {
                    "model_id": "qwen-edit",
                    "files": [
                        {"name": "qwen_edit.gguf", "kind": "unet"},
                        {"name": "qwen_te.safetensors", "kind": "clip"},
                        {"name": "qwen_vae.safetensors", "kind": "vae"},
                    ],
                },
                "native_alpha": {
                    "workflow": "object-gen-native-alpha-v1",
                    "manifest": {
                        "model_id": "qwen-layered",
                        "files": [
                            {"name": "layered.gguf", "kind": "unet"},
                            {"name": "qwen_te.safetensors", "kind": "clip"},
                            {"name": "layered_vae.safetensors", "kind": "vae"},
                        ],
                    },
                },
            },
            {
                "id": "flux2-dev",
                "label": "FLUX.2",
                "workflow_gen": "object-gen-flux2-v1",
                "workflow_inpaint": "object-inpaint-flux2-v1",
                "supports_negative": False,
                "sampling": {"steps": 20, "cfg": 4.0},
                "manifest": {
                    "model_id": "flux2",
                    "files": [
                        {"name": "flux2.gguf", "kind": "unet"},
                        {"name": "mistral.safetensors", "kind": "clip"},
                        {"name": "flux2_vae.safetensors", "kind": "vae"},
                    ],
                },
            },
        ],
    }
    data.update(overrides)
    return ModelProfileRegistry.model_validate(data)


def test_리포의_프로파일_파일이_스키마를_통과하고_핀돼_있다():
    registry = load_model_profiles(REPO_ROOT / "model_profiles.yaml")
    assert {p.id for p in registry.profiles} == {"qwen-image", "flux2-dev"}
    assert registry.get(None).id == "qwen-image"  # 기본값
    assert registry.get("flux2-dev").manifest.pinned
    assert registry.get("qwen-image").native_alpha is not None


def test_기본_프로파일은_qwen_gen_워크플로우를_쓴다():
    build = build_job(
        BriefInput.model_validate(BRIEF_BASE), {"test-pack": make_pack()}, profiles=make_registry()
    )
    assert build.primary.workflow_ref.id == "object-gen-qwen-v1"
    assert build.primary.manifest.model_id == "qwen"
    graph = build.graph_for(None)
    assert graph["1"]["inputs"]["unet_name"] == "qwen.gguf"


def test_flux2_선택시_flux2_워크플로우와_매니페스트로_빌드된다():
    brief = BriefInput.model_validate({**BRIEF_BASE, "model": "flux2-dev"})
    build = build_job(brief, {"test-pack": make_pack()}, profiles=make_registry())
    assert build.primary.workflow_ref.id == "object-gen-flux2-v1"
    payload = build.payload_for("segment", build.seeds)
    assert payload.model_manifest.model_id == "flux2"
    assert payload.graph["2"]["inputs"]["type"] == "flux2"


def test_알_수_없는_모델은_거부한다():
    brief = BriefInput.model_validate({**BRIEF_BASE, "model": "sdxl"})
    with pytest.raises(OrchestrationError):
        build_job(brief, {"test-pack": make_pack()}, profiles=make_registry())


def test_참조_이미지는_styleref_워크플로우와_edit_매니페스트로_라우팅된다(tmp_path):
    ref = tmp_path / "ref.png"
    Image.fromarray(make_rgba(64, 64, (200, 30, 30, 255)), "RGBA").save(ref)
    brief = BriefInput.model_validate({**BRIEF_BASE, "reference_images": [str(ref)]})
    build = build_job(
        brief,
        {"test-pack": make_pack()},
        profiles=make_registry(),
        workdir=tmp_path / "inputs",
    )
    assert build.primary.workflow_ref.id == "object-gen-styleref-v1"
    assert build.primary.manifest.model_id == "qwen-edit"
    assert "ref1" in build.input_images
    assert (tmp_path / "inputs" / "ref1.png").is_file()  # 재현용 사본
    assert "reference image" in build.prompts.positive
    payload = build.payload_for(None, build.seeds)
    assert payload.graph["6"]["inputs"]["image1"] == ["5", 0]


def test_참조_이미지_2장은_동적으로_두번째_로드가_배선된다(tmp_path):
    refs = []
    for i in range(2):
        p = tmp_path / f"r{i}.png"
        Image.fromarray(make_rgba(64, 64, (10 * i, 30, 30, 255)), "RGBA").save(p)
        refs.append(str(p))
    brief = BriefInput.model_validate({**BRIEF_BASE, "reference_images": refs})
    build = build_job(
        brief, {"test-pack": make_pack()}, profiles=make_registry(), workdir=tmp_path / "in"
    )
    payload = build.payload_for(None, build.seeds)
    assert payload.graph["ref2_load"]["class_type"] == "LoadImage"
    assert payload.graph["6"]["inputs"]["image2"] == ["ref2_load", 0]
    assert set(build.input_images) == {"ref1", "ref2"}


def test_flux2는_참조_이미지를_지원하지_않는다(tmp_path):
    ref = tmp_path / "ref.png"
    Image.fromarray(make_rgba(32, 32, (1, 2, 3, 255)), "RGBA").save(ref)
    brief = BriefInput.model_validate(
        {**BRIEF_BASE, "model": "flux2-dev", "reference_images": [str(ref)]}
    )
    with pytest.raises(OrchestrationError, match="참조 이미지"):
        build_job(brief, {"test-pack": make_pack()}, profiles=make_registry(), workdir=tmp_path)


def test_native_alpha_미지원_프로파일의_translucent은_trimap으로_강등된다():
    pack = make_pack(
        material_class="translucent", matting={"strategy": "native_alpha", "model": "m"}
    )
    brief = BriefInput.model_validate({**BRIEF_BASE, "model": "flux2-dev"})
    build = build_job(brief, {"test-pack": pack}, profiles=make_registry())
    assert build.chain == ["trimap"]
    assert build.primary.workflow_ref.id == "object-gen-flux2-v1"
    payload = build.payload_for("trimap", build.seeds)
    assert any(n.get("class_type") == "ApplyMatting" for n in payload.graph.values())


def test_프로파일이_있으면_스타일_팩_없이도_생성할_수_있다(tmp_path):
    from PIL import Image

    from tests.conftest import make_rgba

    # 참조 이미지 단독
    ref = tmp_path / "ref.png"
    Image.fromarray(make_rgba(64, 64, (200, 30, 30, 255)), "RGBA").save(ref)
    brief = BriefInput.model_validate(
        {**{k: v for k, v in BRIEF_BASE.items() if k != "style"}, "reference_images": [str(ref)]}
    )
    build = build_job(brief, {}, profiles=make_registry(), workdir=tmp_path / "in")
    assert build.primary.workflow_ref.id == "object-gen-styleref-v1"
    assert "글로시 쇼핑백" in build.prompts.positive
    assert build.payload_for(None, build.seeds).style_packs == []

    # 컨셉만 (팩·참조 모두 없음)
    plain = BriefInput.model_validate({k: v for k, v in BRIEF_BASE.items() if k != "style"})
    plain_build = build_job(plain, {}, profiles=make_registry())
    assert plain_build.primary.workflow_ref.id == "object-gen-qwen-v1"
    assert plain_build.material == "opaque"


def test_translucent은_native_alpha_기본에_trimap_폴백_그래프를_가진다():
    pack = make_pack(
        material_class="translucent", matting={"strategy": "native_alpha", "model": "m"}
    )
    build = build_job(
        BriefInput.model_validate(BRIEF_BASE), {"test-pack": pack}, profiles=make_registry()
    )
    assert build.primary.workflow_ref.id == "object-gen-native-alpha-v1"
    assert build.fallback is not None
    assert build.fallback.workflow_ref.id == "object-gen-qwen-v1"
    # native_alpha 페이로드는 레이어드 모델 + 마지막 레이어 회수
    native = build.payload_for("native_alpha", build.seeds)
    assert native.model_manifest.model_id == "qwen-layered"
    assert native.output_index == -1
    # trimap 폴백 페이로드는 일반 모델 + 조각 합성
    fallback = build.payload_for("trimap", build.seeds)
    assert fallback.model_manifest.model_id == "qwen"
    assert fallback.output_index == 0
    assert any(n.get("class_type") == "ApplyMatting" for n in fallback.graph.values())