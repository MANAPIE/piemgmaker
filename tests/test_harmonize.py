import numpy as np
import pytest
from PIL import Image

from piemgmaker.pipeline.harmonize import HarmonizeSpec, harmonize
from piemgmaker.pipeline.paste_back import PlacementOutOfBounds
from piemgmaker.schemas.generation import Placement
from tests.conftest import make_rgba, to_image


def place(x=70, y=70) -> Placement:
    return Placement(x=x, y=y, scale=1.0)


def gradient_scene(width=200, height=200) -> Image.Image:
    """왼쪽 220 → 오른쪽 40으로 어두워지는 불투명 장면 — 광원이 왼쪽."""
    arr = make_rgba(width, height, (0, 0, 0, 255))
    ramp = np.linspace(220, 40, width)
    arr[..., 0] = ramp[None, :]
    arr[..., 1] = ramp[None, :]
    arr[..., 2] = ramp[None, :]
    return to_image(arr)


def flat_asset(size=60, color=(200, 60, 60, 255)) -> np.ndarray:
    return make_rgba(size, size, color)


def spec(**overrides) -> HarmonizeSpec:
    values = dict(strength=1.0, relight=1.0, contact_shadow=0.6, drop_shadow=0.35)
    values.update(overrides)
    return HarmonizeSpec(**values)


def test_strength_0은_입력을_그대로_돌려준다():
    scene = gradient_scene()
    asset = flat_asset()
    out_scene, out_asset, report = harmonize(scene, asset, place(), HarmonizeSpec(strength=0.0))
    assert out_scene is scene
    assert out_asset is asset
    assert "strength=0" in report.skipped


def test_동일_입력은_동일_출력이다():
    a1 = harmonize(gradient_scene(), flat_asset(), place(), spec(noise_seed=42))
    a2 = harmonize(gradient_scene(), flat_asset(), place(), spec(noise_seed=42))
    assert np.array_equal(np.asarray(a1[0]), np.asarray(a2[0]))
    assert np.array_equal(a1[1], a2[1])


def test_리라이트는_광원_쪽을_밝히고_hue를_보존한다():
    _, asset_out, report = harmonize(
        gradient_scene(), flat_asset(), place(), spec(contact_shadow=0.0, drop_shadow=0.0)
    )
    assert report.relight_applied
    # 왼쪽(광원 쪽)이 오른쪽보다 밝아야 한다 — 코어 내부(페더·랩 영향권 밖)에서 비교
    luma = asset_out[..., :3].astype(np.float64).mean(axis=-1)
    assert luma[30, 5:15].mean() > luma[30, 45:55].mean()
    # 붉은 자산의 채널 비율(R>G≈B)이 유지된다 — 선형 곱셈은 hue를 돌리지 않는다
    center = asset_out[30, 30].astype(np.float64)
    original = np.asarray([200.0, 60.0, 60.0])
    assert center[0] > center[1]
    ratio_before = original[0] / original[1]
    ratio_after = center[0] / max(center[1], 1.0)
    assert abs(ratio_after - ratio_before) / ratio_before < 0.12


def test_광원_방향이_리포트에_기록된다():
    _, _, report = harmonize(gradient_scene(), flat_asset(), place(), spec())
    assert report.light_dir is not None
    vx, vy = report.light_dir
    assert vx < -0.5  # 왼쪽이 밝다 → 밝은 쪽 벡터는 -x


def test_그림자는_장면이_있는_픽셀만_어둡게_한다():
    scene = gradient_scene()
    before = np.asarray(scene).astype(np.int64)
    out_scene, _, _ = harmonize(scene, flat_asset(), place(), spec(relight=0.0))
    after = np.asarray(out_scene).astype(np.int64)
    # 자산 경계 바로 바깥(접지 그림자 영역)은 어두워진다
    assert after[100, 68, 0] < before[100, 68, 0]
    # 자산에서 먼 픽셀은 거의 변하지 않는다
    assert abs(int(after[5, 5, 0]) - int(before[5, 5, 0])) <= 2
    # 알파는 그림자의 영향을 받지 않는다
    assert np.array_equal(after[..., 3], before[..., 3])


def test_투명_배경에는_그림자가_생기지_않는다():
    arr = make_rgba(200, 200)  # 전부 투명
    arr[40:160, 40:160] = (150, 150, 150, 255)
    scene = to_image(arr)
    before = np.asarray(scene).copy()
    out_scene, _, _ = harmonize(scene, flat_asset(), place(), spec())
    after = np.asarray(out_scene)
    transparent = before[..., 3] == 0
    assert np.array_equal(after[transparent], before[transparent])


def test_페더는_알파를_침식하지_않고_확장만_한다():
    _, asset_out, _ = harmonize(gradient_scene(), flat_asset(), place(), spec())
    original_alpha = flat_asset()[..., 3]
    # max() 정책 — 원래 불투명이던 픽셀은 그대로 불투명해야 형상 IoU가 유지된다
    assert np.all(asset_out[..., 3].astype(np.int64) >= original_alpha.astype(np.int64) - 1)


def test_그레인은_시드_결정론이고_장면_그레인을_따라간다():
    rng = np.random.default_rng(0)
    arr = np.asarray(gradient_scene()).copy()
    noise = rng.normal(0.0, 12.0, arr.shape[:2])
    rgb = arr[..., :3].astype(np.float64) + noise[..., None]
    arr[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    noisy_scene = to_image(arr)

    _, out_a, report_a = harmonize(noisy_scene, flat_asset(), place(), spec(noise_seed=1))
    _, out_a2, _ = harmonize(noisy_scene, flat_asset(), place(), spec(noise_seed=1))
    _, out_b, _ = harmonize(noisy_scene, flat_asset(), place(), spec(noise_seed=2))
    assert report_a.grain_sigma > 0
    assert np.array_equal(out_a, out_a2)
    assert not np.array_equal(out_a, out_b)


def test_대형_자산도_다운샘플_경로에서_광원_방향이_유지된다():
    """σ > 32 → 다운샘플 근사 경로. 방향성·결정론이 원해상도 경로와 동일해야 한다."""
    scene = gradient_scene(400, 400)
    asset = flat_asset(280)  # 대각 ~396 → σ ~99 → factor 3
    pl = Placement(x=60, y=60, scale=1.0)
    _, out_a, report = harmonize(scene, asset, pl, spec(contact_shadow=0.0, drop_shadow=0.0))
    assert report.relight_applied
    assert report.light_dir is not None and report.light_dir[0] < -0.5
    luma = out_a[..., :3].astype(np.float64).mean(axis=-1)
    assert luma[140, 20:60].mean() > luma[140, 220:260].mean()  # 광원 쪽(왼쪽)이 밝다
    _, out_b, _ = harmonize(scene, asset, pl, spec(contact_shadow=0.0, drop_shadow=0.0))
    assert np.array_equal(out_a, out_b)


def test_링_표본이_없으면_리라이트를_건너뛴다():
    scene = to_image(make_rgba(60, 60, (100, 100, 100, 255)))
    asset = flat_asset(60)  # 장면 전체를 덮어 링이 없다
    _, _, report = harmonize(scene, asset, Placement(x=0, y=0, scale=1.0), spec())
    assert not report.relight_applied
    assert any("링 표본 부족" in s for s in report.skipped)


def test_캔버스를_벗어나는_배치는_거부한다():
    with pytest.raises(PlacementOutOfBounds):
        harmonize(gradient_scene(), flat_asset(), place(x=180, y=180), spec())


def striped_scene(width=200, height=200) -> Image.Image:
    """세로 줄무늬 질감 장면 — imprint 질감 투과 검증용."""
    arr = make_rgba(width, height, (0, 0, 0, 255))
    base = np.full(width, 150.0)
    base[::8] = 100.0  # 8px 주기 어두운 골
    arr[..., 0] = base[None, :]
    arr[..., 1] = base[None, :]
    arr[..., 2] = base[None, :]
    return to_image(arr)


def imprint_spec(**overrides) -> HarmonizeSpec:
    values = dict(strength=1.0, mode="imprint", imprint_texture=1.0, imprint_emboss=0.5)
    values.update(overrides)
    return HarmonizeSpec(**values)


def test_imprint는_장면_질감이_로고를_관통한다():
    _, asset_out, report = harmonize(striped_scene(), flat_asset(), place(), imprint_spec())
    luma = asset_out[..., :3].astype(np.float64).mean(axis=-1)
    row = luma[30, 8:52]
    # 평탄한 로고였는데 출력엔 줄무늬 주기의 명암이 생긴다
    assert row.max() - row.min() > 5
    assert any("imprint" in s for s in report.skipped)


def test_imprint는_hue를_보존하고_그림자를_만들지_않는다():
    scene = striped_scene()
    before = np.asarray(scene).copy()
    scene_out, asset_out, _ = harmonize(scene, flat_asset(), place(), imprint_spec())
    # 장면은 변형하지 않는다 (새김은 그림자가 없다)
    assert np.array_equal(np.asarray(scene_out), before)
    # 붉은 로고의 채널 대소가 유지된다 — 곱셈 변조라 hue 불변
    center = asset_out[30, 30].astype(np.float64)
    assert center[0] > center[1] and center[0] > center[2]


def test_imprint는_잉크_불투명도로_알파를_낮춘다():
    _, asset_out, _ = harmonize(
        striped_scene(), flat_asset(), place(), imprint_spec(ink_opacity=0.9)
    )
    # 코어 알파 255 → 255×0.9 근방 (페더 확장 max()가 있어 이하로 내려가진 않음)
    core_alpha = int(asset_out[30, 30, 3])
    assert 225 <= core_alpha <= 232
