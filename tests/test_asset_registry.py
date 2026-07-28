import io

import pytest
from PIL import Image

from piemgmaker.assets_lib.library import AssetLibrary, AssetLibraryError
from piemgmaker.assets_lib.registry import AssetRegistry, AssetRegistryError
from tests.conftest import make_rgba


def png_bytes(width=64, height=64, color=(20, 40, 200, 255)) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(make_rgba(width, height, color), "RGBA").save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def registry(tmp_path) -> AssetRegistry:
    return AssetRegistry(tmp_path / "assets")


def test_등록하면_파일과_매니페스트가_생기고_라이브러리에서_해석된다(registry):
    asset = registry.register_asset(
        asset_id="badge",
        name="배지",
        asset_type="object",
        variant_id="main",
        data=png_bytes(),
        min_scale=0.5,
        clear_space_px=8,
    )
    assert asset.usage.clear_space_px == 8
    library = AssetLibrary(registry.root)
    resolved, variant, path = library.resolve("badge")
    assert variant.id == "main" and path.is_file()


def test_불투명_코어_없는_PNG는_등록을_거부한다(registry):
    translucent = png_bytes(color=(10, 10, 10, 200))
    with pytest.raises(AssetRegistryError, match="불투명"):
        registry.register_asset(
            asset_id="ghost", name="고스트", asset_type="object", variant_id="main", data=translucent
        )


def test_가는_획_워드마크_로고도_등록된다(registry):
    """텍스트 로고(획 폭 수 px) — 침식 적응 축소로 코어를 확보해 등록 가능해야 한다."""
    import io

    import numpy as np
    from PIL import Image

    arr = np.zeros((60, 240, 4), dtype=np.uint8)
    for y0 in (12, 28, 44):  # 가로 획 3개, 두께 3px
        arr[y0 : y0 + 3, 10:230] = (12, 12, 12, 255)
    buf = io.BytesIO()
    Image.fromarray(arr, "RGBA").save(buf, format="PNG")
    asset = registry.register_asset(
        asset_id="wordmark", name="워드마크", asset_type="logo", variant_id="main", data=buf.getvalue()
    )
    assert asset.type == "logo"


def test_PNG가_아니면_거부한다(registry):
    buf = io.BytesIO()
    Image.fromarray(make_rgba(64, 64, (1, 2, 3, 255))[..., :3], "RGB").save(buf, format="JPEG")
    with pytest.raises(AssetRegistryError, match="PNG"):
        registry.register_asset(
            asset_id="jpeg", name="j", asset_type="object", variant_id="main", data=buf.getvalue()
        )


def test_중복_id와_잘못된_id는_거부한다(registry):
    registry.register_asset(
        asset_id="badge", name="배지", asset_type="object", variant_id="main", data=png_bytes()
    )
    with pytest.raises(AssetRegistryError, match="이미 존재"):
        registry.register_asset(
            asset_id="badge", name="배지2", asset_type="object", variant_id="alt", data=png_bytes()
        )
    with pytest.raises(AssetRegistryError, match="kebab"):
        registry.register_asset(
            asset_id="Bad_ID", name="x", asset_type="object", variant_id="main", data=png_bytes()
        )


def test_variant_추가와_로고_변형_등록(registry):
    registry.register_asset(
        asset_id="link-logo", name="LINK", asset_type="logo", variant_id="flat", data=png_bytes()
    )
    asset = registry.add_variant("link-logo", "3d-lettering", png_bytes(color=(90, 10, 10, 255)))
    assert [v.id for v in asset.variants] == ["flat", "3d-lettering"]


def test_soft_delete는_해석을_차단하고_복원하면_돌아온다(registry):
    registry.register_asset(
        asset_id="badge", name="배지", asset_type="object", variant_id="main", data=png_bytes()
    )
    registry.set_archived("badge", None, True)

    library = AssetLibrary(registry.root)
    with pytest.raises(AssetLibraryError, match="숨김"):
        library.resolve("badge")
    # 파일은 남아 있다 (soft delete)
    assert (registry.root / "badge" / "main.png").is_file()

    registry.set_archived("badge", None, False)
    assert AssetLibrary(registry.root).resolve("badge")[1].id == "main"


def test_variant_단위_숨김은_다음_활성_variant로_폴백한다(registry):
    registry.register_asset(
        asset_id="badge", name="배지", asset_type="object", variant_id="main", data=png_bytes()
    )
    registry.add_variant("badge", "alt", png_bytes(color=(5, 200, 5, 255)))
    registry.set_archived("badge", "main", True)

    library = AssetLibrary(registry.root)
    assert library.resolve("badge")[1].id == "alt"  # 미지정 ref는 활성 variant로
    with pytest.raises(AssetLibraryError, match="숨김"):
        library.resolve("badge:main")