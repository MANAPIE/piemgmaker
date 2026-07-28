from pathlib import Path

import pytest
from pydantic import ValidationError

from piemgmaker.schemas.asset import Asset, AssetManifest
from piemgmaker.schemas.brief import SIZE_PRESETS, BriefInput, SizeSpec, StyleInput
from piemgmaker.schemas.generation import (
    JobPayload,
    Placement,
    Prompts,
    ResolvedAsset,
    WorkflowRef,
)
from piemgmaker.schemas.style_pack import ModelManifest, StylePack, load_style_packs

REPO_ROOT = Path(__file__).parent.parent

MINIMAL_BRIEF = {"campaign_text": "여름 세일 캠페인", "object_concept": "3D 쇼핑백과 % 기호"}


def make_pack(**overrides) -> StylePack:
    data = {
        "id": "test-pack",
        "version": "0.1.0",
        "prompt_template": "{subject}, test style, {mood}",
        "material_class": "opaque",
        "shadow_policy": "none",
        "workflow_template": "object-gen-v1",
        "model_manifest": {"model_id": "m"},
        "matting": {"strategy": "segment", "model": "m"},
    }
    data.update(overrides)
    return StylePack.model_validate(data)


class TestBriefInput:
    def test_최소_입력으로_기본값이_채워진다(self):
        brief = BriefInput.model_validate(MINIMAL_BRIEF)
        assert brief.size().width == 1024 and brief.size().height == 1024
        assert brief.candidate_count == 4
        assert brief.seed == "random"
        assert brief.style.consistency_guaranteed

    def test_campaign_text_500자_초과는_거부한다(self):
        with pytest.raises(ValidationError):
            BriefInput.model_validate({**MINIMAL_BRIEF, "campaign_text": "가" * 501})

    def test_candidate_count는_1이상_8이하다(self):
        for bad in (0, 9):
            with pytest.raises(ValidationError):
                BriefInput.model_validate({**MINIMAL_BRIEF, "candidate_count": bad})

    def test_알_수_없는_사이즈_프리셋은_거부한다(self):
        with pytest.raises(ValidationError):
            BriefInput.model_validate({**MINIMAL_BRIEF, "size_preset": "2:1"})

    def test_커스텀_사이즈는_상한과_8의_배수를_검증한다(self):
        ok = BriefInput.model_validate(
            {**MINIMAL_BRIEF, "size_preset": {"width": 1600, "height": 800}}
        )
        assert ok.size() == SizeSpec(width=1600, height=800)
        for bad in ({"width": 2056, "height": 800}, {"width": 1001, "height": 800}, {"width": 248, "height": 800}):
            with pytest.raises(ValidationError):
                BriefInput.model_validate({**MINIMAL_BRIEF, "size_preset": bad})

    def test_자산_참조는_kebab_형식만_허용한다(self):
        ok = BriefInput.model_validate({**MINIMAL_BRIEF, "assets": ["link-logo:3d-lettering"]})
        assert ok.assets == ["link-logo:3d-lettering"]
        with pytest.raises(ValidationError):
            BriefInput.model_validate({**MINIMAL_BRIEF, "assets": ["Link_Logo"]})

    def test_시드는_음수를_거부하고_random을_허용한다(self):
        assert BriefInput.model_validate({**MINIMAL_BRIEF, "seed": 42}).seed == 42
        with pytest.raises(ValidationError):
            BriefInput.model_validate({**MINIMAL_BRIEF, "seed": -1})

    def test_참조_이미지는_최대_2장이다(self):
        with pytest.raises(ValidationError):
            BriefInput.model_validate(
                {**MINIMAL_BRIEF, "reference_images": ["a.png", "b.png", "c.png"]}
            )

    def test_사이즈_프리셋은_규격과_일치한다(self):
        assert SIZE_PRESETS == {
            "1:1": (1024, 1024),
            "4:3": (1152, 864),
            "16:9": (1344, 768),
            "3:4": (864, 1152),
        }


class TestStyleInput:
    def test_팩_혼합이나_자유_입력은_일관성_비보증이다(self):
        assert StyleInput(style_packs=["a"]).consistency_guaranteed
        assert not StyleInput(style_packs=["a", "b"]).consistency_guaranteed
        assert not StyleInput(style_packs=["a"], free_text="grunge").consistency_guaranteed


class TestStylePack:
    def test_subject_슬롯이_없으면_거부한다(self):
        with pytest.raises(ValidationError):
            make_pack(prompt_template="no slots here")

    def test_허용되지_않은_슬롯은_거부한다(self):
        with pytest.raises(ValidationError):
            make_pack(prompt_template="{subject} {brand}")

    def test_render_prompt는_슬롯을_채우고_공백을_정리한다(self):
        pack = make_pack()
        assert pack.render_prompt(subject="shopping bag", mood="") == "shopping bag, test style,"

    def test_리포의_스타일_팩_전체가_스키마를_통과하고_표시명을_가진다(self):
        packs = load_style_packs(REPO_ROOT / "style_packs")
        assert len(packs) == 12
        # 기본 5종은 반드시 존재
        assert {"sp-3d-glossy", "sp-3d-clay", "sp-flat-illust", "sp-isometric", "sp-glass"} <= set(packs)
        assert all(p.name for p in packs.values())  # 전 팩 표시명 필수 (id 노출 방지)
        assert packs["sp-glass"].display_name == "글래스"
        assert packs["sp-glass"].material_class == "translucent"
        assert packs["sp-glass"].matting.strategy == "native_alpha"


class TestAssetManifest:
    def test_자산_id_중복은_거부한다(self):
        asset = {
            "id": "logo",
            "name": "로고",
            "type": "logo",
            "variants": [{"id": "main", "file": "logo/main.png"}],
        }
        with pytest.raises(ValidationError):
            AssetManifest.model_validate({"assets": [asset, asset]})

    def test_variant_id_중복은_거부한다(self):
        with pytest.raises(ValidationError):
            Asset.model_validate(
                {
                    "id": "logo",
                    "name": "로고",
                    "type": "logo",
                    "variants": [
                        {"id": "main", "file": "a.png"},
                        {"id": "main", "file": "b.png"},
                    ],
                }
            )


class TestJobPayload:
    def test_자산이_있으면_protected_asset이_참이_된다(self):
        base = dict(
            job_id="j1",
            workflow=WorkflowRef(id="object-gen-v1", hash="h"),
            graph={},
            model_manifest=ModelManifest(model_id="m"),
            matting_chain=["segment"],
            seeds=[1],
            size=SizeSpec(width=1024, height=1024),
            prompts=Prompts(positive="p"),
        )
        assert not JobPayload(**base).protected_asset
        with_asset = JobPayload(
            **base,
            assets=[
                ResolvedAsset(
                    asset_id="logo",
                    variant_id="main",
                    file="logo/main.png",
                    file_sha256="0" * 64,
                    placement=Placement(x=0, y=0, scale=1.0),
                )
            ],
        )
        assert with_asset.protected_asset
