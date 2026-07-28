import numpy as np
import pytest
from PIL import Image

from piemgmaker.pipeline.paste_back import (
    AssetCoreEmpty,
    PlacementOutOfBounds,
    core_mask,
    make_inpaint_mask,
    paste_back,
    scale_asset,
    verify_core_hash,
)
from piemgmaker.schemas.generation import Placement
from tests.conftest import make_rgba, to_image


def place(x=10, y=10, scale=1.0) -> Placement:
    return Placement(x=x, y=y, scale=scale)


def test_코어는_완전_불투명_영역의_침식이다(opaque_asset):
    core = core_mask(opaque_asset, band_px=8)
    assert core[20, 20]
    assert not core[0, 0]
    assert int(core.sum()) == 24 * 24


def test_반투명뿐인_자산은_거부한다():
    translucent = make_rgba(32, 32, (10, 10, 10, 200))  # 코어 임계(250) 미만
    with pytest.raises(AssetCoreEmpty):
        core_mask(translucent)


def test_가는_획_로고는_침식_반경을_줄여_코어를_얻는다():
    """워드마크형 로고 — 획 폭 4px이라 8px 침식으로는 코어가 없지만 적응 축소로 확보."""
    logo = make_rgba(120, 40)
    logo[10:14, 8:112] = (10, 10, 10, 255)  # 가로 획 4px
    logo[24:28, 8:112] = (10, 10, 10, 255)
    core = core_mask(logo, band_px=8)
    assert core.any()
    assert not core[0, 0]  # 투명 영역은 코어 아님

    gen = to_image(make_rgba(200, 100, (90, 90, 90, 128)))
    result = paste_back(gen, logo, place(x=20, y=30))
    assert verify_core_hash(result, logo, place(x=20, y=30))


def test_앤티앨리어싱으로_254가_된_코어도_인정한다():
    soft_export = make_rgba(40, 40, (30, 30, 30, 254))
    assert core_mask(soft_export).any()


def test_paste_back_후_코어_해시_검증이_통과한다(opaque_asset):
    gen = to_image(make_rgba(128, 128, (90, 90, 90, 128)))
    result = paste_back(gen, opaque_asset, place())
    assert verify_core_hash(result, opaque_asset, place())


def test_코어_픽셀_변조는_해시_검증에_걸린다(opaque_asset):
    gen = to_image(make_rgba(128, 128, (90, 90, 90, 128)))
    result = np.asarray(paste_back(gen, opaque_asset, place())).copy()
    result[30, 30, 0] ^= 1  # 코어 내부 1비트 변조
    assert not verify_core_hash(Image.fromarray(result, "RGBA"), opaque_asset, place())


def test_전이_밴드_변형은_코어_검증에_영향이_없다(opaque_asset):
    gen = to_image(make_rgba(128, 128, (90, 90, 90, 128)))
    result = np.asarray(paste_back(gen, opaque_asset, place())).copy()
    result[10, 10] = (0, 0, 0, 0)  # 자산 영역이지만 코어 밖(밴드)
    assert verify_core_hash(Image.fromarray(result, "RGBA"), opaque_asset, place())


def test_캔버스를_벗어나는_배치는_거부한다(opaque_asset):
    gen = to_image(make_rgba(64, 64))
    with pytest.raises(PlacementOutOfBounds):
        paste_back(gen, opaque_asset, place(x=40, y=40))


def test_스케일된_자산도_결정론적으로_검증된다(opaque_asset):
    scaled = scale_asset(to_image(opaque_asset), 0.5)
    assert scaled.shape[:2] == (20, 20)
    gen = to_image(make_rgba(64, 64))
    result = paste_back(gen, scale_asset(to_image(opaque_asset), 0.5), place(x=5, y=5))
    assert verify_core_hash(result, scaled, place(x=5, y=5))


def test_2단_마스크는_코어0_외부255_밴드는_램프다(opaque_asset):
    mask = np.asarray(make_inpaint_mask((128, 128), opaque_asset, place(), band_px=8))
    assert mask[30, 30] == 0  # 코어 (자산 중심 부근)
    assert mask[0, 0] == 255  # 외부
    # 코어는 캔버스 좌표 18..41 — 그 바깥 3픽셀 지점은 램프 밴드
    ring_value = int(mask[30, 44])
    assert 0 < ring_value < 255
