import numpy as np
import pytest
from PIL import Image

from piemgmaker.pipeline.paste_back import (
    AssetCoreEmpty,
    PlacementOutOfBounds,
    core_mask,
    make_inpaint_mask,
    measure_asset_fidelity,
    paste_back,
    scale_asset,
    transform_asset,
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


def test_core_value는_코어를_저강도로_열고_램프_하한이_된다(opaque_asset):
    mask = np.asarray(
        make_inpaint_mask((128, 128), opaque_asset, place(), band_px=8, core_value=40)
    )
    assert mask[30, 30] == 40  # 코어가 완전 보호(0)가 아니라 저강도 재통합
    assert mask[0, 0] == 255
    band = mask[(mask > 40) & (mask < 255)]
    assert band.size and band.min() > 40  # 램프는 core_value→255 보간 — 코어보다 낮아지지 않는다


def test_smoothstep_램프는_단조증가이고_경계에서_완만하다(opaque_asset):
    linear = np.asarray(
        make_inpaint_mask((128, 128), opaque_asset, place(), band_px=8, ramp="linear")
    )
    smooth = np.asarray(
        make_inpaint_mask((128, 128), opaque_asset, place(), band_px=8, ramp="smoothstep")
    )
    # 같은 행에서 코어 바깥으로 나갈수록 단조증가
    row = smooth[30, 42:52].astype(np.int64)
    assert np.all(np.diff(row) >= 0)
    # smoothstep은 밴드 시작(코어 인접)에서 linear보다 낮다 — 경계 전이가 완만하다
    assert smooth[30, 43] < linear[30, 43]


def test_blend_정책은_완전_불투명_자산에서_force와_동일하다(opaque_asset):
    gen = to_image(make_rgba(128, 128, (90, 90, 90, 255)))
    forced = np.asarray(paste_back(gen, opaque_asset, place(), core_policy="force"))
    blended = np.asarray(paste_back(gen, opaque_asset, place(), core_policy="blend"))
    assert np.array_equal(forced, blended)


def test_fidelity는_무변조_합성에서_만점이다(opaque_asset):
    gen = to_image(make_rgba(128, 128, (90, 90, 90, 255)))
    result = paste_back(gen, opaque_asset, place(), core_policy="blend")
    report = measure_asset_fidelity(result, opaque_asset, opaque_asset, place())
    assert report.passed
    assert report.shape_iou == 1.0
    assert report.mean_delta_e < 0.5
    assert report.hue_shift_deg < 0.5


def test_fidelity는_hue_회전을_잡아낸다(opaque_asset):
    # 파랑(20,40,200) → 채널 스왑으로 붉은 계열 — 밝기는 비슷해도 hue가 크게 돈다
    rotated = opaque_asset.copy()
    rotated[..., 0], rotated[..., 2] = opaque_asset[..., 2], opaque_asset[..., 0]
    gen = to_image(make_rgba(128, 128, (90, 90, 90, 255)))
    result = paste_back(gen, rotated, place(), core_policy="blend")
    report = measure_asset_fidelity(result, rotated, opaque_asset, place())
    assert not report.passed
    assert report.hue_shift_deg > 45


def test_fidelity는_저강도_휘도_변조를_통과시킨다(opaque_asset):
    # 리라이트 수준(±10% 미만 휘도)의 변조는 상한 안이다
    modulated = opaque_asset.copy()
    rgb = modulated[..., :3].astype(np.float64)
    modulated[..., :3] = np.clip(rgb * 1.08, 0, 255).astype(np.uint8)
    gen = to_image(make_rgba(128, 128, (90, 90, 90, 255)))
    result = paste_back(gen, modulated, place(), core_policy="blend")
    report = measure_asset_fidelity(result, modulated, opaque_asset, place())
    assert report.passed
    assert 0 < report.mean_delta_e < 12


def test_transform_회전은_결정론이고_bbox가_회전을_반영한다(opaque_asset):
    tall = make_rgba(20, 60, (10, 10, 10, 255))  # 20x60 세로 자산
    a1, quad1 = transform_asset(to_image(tall), 1.0, rotation_deg=90.0)
    a2, quad2 = transform_asset(to_image(tall), 1.0, rotation_deg=90.0)
    assert np.array_equal(a1, a2)
    assert quad1 is None and quad2 is None
    h, w = a1.shape[:2]
    assert w > h  # 90도 회전으로 가로가 길어진다


def test_transform_틸트는_사변형을_기록하고_모서리가_기운다():
    square = make_rgba(80, 80, (10, 10, 10, 255))
    warped, quad = transform_asset(to_image(square), 1.0, tilt="slight")
    assert quad == ((0.05, 0.0), (0.95, 0.0), (1.0, 1.0), (0.0, 1.0))
    # 윗변이 5%씩 안쪽으로 — 좌상단 모서리는 투명, 아랫변 모서리는 불투명
    assert warped[0, 0, 3] < 128
    assert warped[-1, 0, 3] >= 128


def test_회전된_자산도_paste_back_코어_검증이_통과한다(opaque_asset):
    rotated, _ = transform_asset(to_image(opaque_asset), 1.0, rotation_deg=30.0)
    gen = to_image(make_rgba(200, 200, (90, 90, 90, 255)))
    result = paste_back(gen, rotated, place(x=50, y=50))
    assert verify_core_hash(result, rotated, place(x=50, y=50))


def test_transform_스케일은_변환_후_크기에_적용된다():
    tall = make_rgba(40, 120, (10, 10, 10, 255))
    full, _ = transform_asset(to_image(tall), 1.0, rotation_deg=90.0)
    half, _ = transform_asset(to_image(tall), 0.5, rotation_deg=90.0)
    assert half.shape[0] == round(full.shape[0] * 0.5)
    assert half.shape[1] == round(full.shape[1] * 0.5)
