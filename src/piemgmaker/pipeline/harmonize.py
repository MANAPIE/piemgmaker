"""자산-장면 하모나이즈 (결정론): 조명장 → 그림자 → 리라이트 → 라이트랩 → 그레인.

scene과 asset을 각각 변형만 하고 합성은 하지 않는다 — 합성은 paste_back이 소유한다.
난수는 명시 시드의 default_rng만 사용해 동일 입력 → 동일 출력을 보장한다.
큰 반경 연산은 scipy.ndimage(EDT·가우시안)를 쓴다 — _morph의 O(r) 반복은
밴드 폭 수십 픽셀에서 느리다.
"""

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from PIL import Image
from scipy.ndimage import distance_transform_edt, gaussian_filter, zoom

from piemgmaker.pipeline.paste_back import PlacementOutOfBounds
from piemgmaker.schemas.generation import Placement

# 휘도 계수(Rec.709) — 조명장·그레인 통계에 공통 사용
_LUMA_W = np.asarray([0.2126, 0.7152, 0.0722], dtype=np.float64)
_GAMMA = 2.2  # 리라이트 곱셈용 근사 선형화 — 정확 sRGB 대신 단순·결정론 우선

MIN_RING_PIXELS = 64  # 이보다 표본이 적으면 조명장 추정을 신뢰하지 않는다
RING_SCENE_ALPHA = 64  # 링 표본으로 인정할 장면 알파 하한
GRAIN_HP_SIGMA = 1.5  # 그레인 통계용 고역 필터 시그마
# 조명장 σ가 이보다 크면 다운샘플 근사 — 가우시안 커널 폭이 σ에 비례해
# 원해상도 비용이 초 단위로 커진다 (2048²·σ500 실측 8.4s → 근사 0.01s대)
LIGHT_FIELD_DOWNSAMPLE_SIGMA = 32.0


@dataclass(frozen=True)
class HarmonizeSpec:
    strength: float = 0.0  # 0 = off (하모나이즈 없이 입력 그대로 반환)
    mode: Literal["overlay", "imprint"] = "overlay"
    relight: float = 0.5
    contact_shadow: float = 0.6
    drop_shadow: float = 0.35
    light_wrap: float = 0.3
    grain_match: bool = True
    ring_px: int = 48  # 조명장 표본 링 폭
    max_luma_gain: float = 0.35  # 휘도 변조 상한 (±비율)
    noise_seed: int = 0
    # imprint 전용 — 표면 '새김': 장면 질감·음영이 로고를 관통하고 경계에 데보스 음영
    imprint_texture: float = 0.85  # 질감 투과 지수 (0=없음, 1=완전 투과)
    imprint_emboss: float = 0.5  # 경계 눌림(데보스) 음영 강도
    ink_opacity: float = 0.9  # 잉크 불투명도 — 표면 톤이 살짝 비치게


@dataclass
class HarmonizeReport:
    light_dir: tuple[float, float] | None = None  # 밝은 쪽을 향하는 단위 벡터 (x, y)
    ring_pixels: int = 0
    relight_applied: bool = False
    grain_sigma: float = 0.0
    skipped: list[str] = field(default_factory=list)


def _luma(rgb: np.ndarray) -> np.ndarray:
    return rgb @ _LUMA_W


def _normalized_blur(values: np.ndarray, weights: np.ndarray, sigma: float) -> np.ndarray:
    """가중 가우시안(normalized convolution) — 표본 없는 영역으로 매끄럽게 외삽한다.

    values가 (H,W,C)면 채널축은 블러하지 않고 2D 가중치를 채널마다 적용한다.
    """
    if values.ndim == weights.ndim + 1:
        num = gaussian_filter(values * weights[..., None], (sigma, sigma, 0), mode="nearest")
        den = gaussian_filter(weights, sigma, mode="nearest")[..., None]
    else:
        num = gaussian_filter(values * weights, sigma, mode="nearest")
        den = gaussian_filter(weights, sigma, mode="nearest")
    return num / np.maximum(den, 1e-6)


def estimate_light_field(
    scene_rgba: np.ndarray,
    presence_canvas: np.ndarray,
    ring_px: int,
    sigma: float,
) -> tuple[np.ndarray, np.ndarray]:
    """자산 주변 링의 휘도를 자산 안쪽까지 외삽한 저주파 조명장 L과 링 마스크를 반환.

    σ가 크면(대형 자산) 다운샘플 → 블러 → 쌍선형 업샘플로 근사한다 — 결과가
    저주파 장이라 시각 차이가 없고, 커널 폭이 σ에 비례하는 원해상도 비용을 피한다.
    """
    dist = distance_transform_edt(~presence_canvas)
    ring = (dist > 0) & (dist <= ring_px) & (scene_rgba[..., 3] >= RING_SCENE_ALPHA)
    luma = _luma(scene_rgba[..., :3].astype(np.float64))
    weights = ring.astype(np.float64)

    factor = min(8, max(1, int(sigma // LIGHT_FIELD_DOWNSAMPLE_SIGMA)))
    if factor > 1:
        small = _normalized_blur(luma[::factor, ::factor], weights[::factor, ::factor], sigma / factor)
        light = zoom(
            small,
            (luma.shape[0] / small.shape[0], luma.shape[1] / small.shape[1]),
            order=1,
            mode="nearest",
        )
        # zoom 반올림으로 1px 어긋날 수 있다 — 잘라내고 모자라면 가장자리 복제
        light = light[: luma.shape[0], : luma.shape[1]]
        pad_y = luma.shape[0] - light.shape[0]
        pad_x = luma.shape[1] - light.shape[1]
        if pad_y or pad_x:
            light = np.pad(light, ((0, pad_y), (0, pad_x)), mode="edge")
    else:
        light = _normalized_blur(luma, weights, sigma)
    return light, ring


def _light_direction(light: np.ndarray, ring: np.ndarray) -> tuple[float, float] | None:
    gy, gx = np.gradient(light)
    vx = float(gx[ring].mean())
    vy = float(gy[ring].mean())
    norm = float(np.hypot(vx, vy))
    if norm < 1e-9:
        return None
    return vx / norm, vy / norm


def _apply_shadows(
    scene: np.ndarray,
    presence_canvas: np.ndarray,
    alpha_canvas: np.ndarray,
    light_dir: tuple[float, float] | None,
    spec: HarmonizeSpec,
    diag: float,
    report: HarmonizeReport,
) -> np.ndarray:
    """접지(등방) + 드롭(방향성) 그림자를 장면 RGB에 곱셈 어둡게 합성한다."""
    contact_eff = spec.contact_shadow * spec.strength
    drop_eff = spec.drop_shadow * spec.strength
    darkening = np.zeros(scene.shape[:2], dtype=np.float64)

    if contact_eff > 0:
        dist = distance_transform_edt(~presence_canvas)
        sigma_c = float(np.clip(0.05 * diag, 3.0, 16.0))
        darkening += contact_eff * np.exp(-dist / sigma_c) * (dist > 0)

    if drop_eff > 0:
        if light_dir is None:
            report.skipped.append("drop_shadow: 광원 방향 미검출")
        else:
            sigma_d = float(np.clip(0.06 * diag, 4.0, 24.0))
            offset = max(2, round(0.04 * diag))
            dx = round(-light_dir[0] * offset)
            dy = round(-light_dir[1] * offset)
            shifted = np.zeros_like(alpha_canvas)
            h, w = alpha_canvas.shape
            src_y = slice(max(0, -dy), min(h, h - dy))
            src_x = slice(max(0, -dx), min(w, w - dx))
            dst_y = slice(max(0, dy), min(h, h + dy))
            dst_x = slice(max(0, dx), min(w, w + dx))
            shifted[dst_y, dst_x] = alpha_canvas[src_y, src_x]
            blurred = gaussian_filter(shifted, sigma_d, mode="nearest")
            darkening += drop_eff * blurred * ~presence_canvas

    darkening = np.clip(darkening, 0.0, 0.85)
    # 장면이 실제로 존재하는 픽셀에만 — 투명 배경에 그림자가 뜨면 안 된다
    visible = scene[..., 3] > 0
    out = scene.copy()
    out[..., :3] = np.where(
        visible[..., None], out[..., :3] * (1.0 - darkening[..., None]), out[..., :3]
    )
    return out


def _relight_asset(
    asset: np.ndarray,
    light_region: np.ndarray,
    ring_mean: float,
    spec: HarmonizeSpec,
) -> np.ndarray:
    """조명장 비율로 자산 휘도를 변조한다. 선형 RGB 곱셈이라 hue·채도가 보존된다."""
    delta = spec.max_luma_gain * spec.relight * spec.strength
    gain = np.clip(light_region / max(ring_mean, 1e-6), 1.0 - delta, 1.0 + delta)
    out = asset.copy()
    rgb = out[..., :3] / 255.0
    linear = np.power(rgb, _GAMMA) * gain[..., None]
    out[..., :3] = np.power(np.clip(linear, 0.0, 1.0), 1.0 / _GAMMA) * 255.0
    return out


def _edge_blend(
    asset: np.ndarray,
    scene_region_rgb: np.ndarray,
    spec: HarmonizeSpec,
    expand: bool = True,
) -> np.ndarray:
    """엣지 페더(바깥 방향으로만 확장) + 라이트랩. 코어(고알파)는 건드리지 않는다.

    imprint 모드는 expand=False — 새김의 경계는 표면이 정의하므로 스커트를 만들지
    않는다. 가는 획(전단된 워드마크)에서 확장 페더가 획 사이를 이어붙여 형상 IoU를
    깨뜨리는 것도 막는다.
    """
    alpha = asset[..., 3] / 255.0
    sigma_f = 0.5 + 0.5 * spec.strength
    if expand:
        blurred_alpha = gaussian_filter(alpha, sigma_f, mode="nearest")
        # max()로 확장만 한다 — 침식하면 형상 IoU가 깨지고 가는 획이 사라진다
        new_alpha = np.maximum(alpha, blurred_alpha)
    else:
        new_alpha = alpha

    # 새로 생긴 스커트 픽셀의 RGB는 인접 자산 색으로 채운다 (투명 픽셀의 검정 침출 방지)
    fill = _normalized_blur(asset[..., :3], alpha, max(sigma_f, 1.0))
    rgb = np.where((alpha < 0.02)[..., None], fill, asset[..., :3].astype(np.float64))

    wrap = spec.light_wrap * spec.strength
    if wrap > 0:
        # 저알파(경계) 픽셀일수록 장면 색을 많이 섞는다 — 코어는 (1-α)≈0이라 불변
        w = wrap * (1.0 - new_alpha) * (new_alpha > 0)
        rgb = rgb * (1.0 - w[..., None]) + scene_region_rgb * w[..., None]

    out = asset.copy()
    out[..., :3] = np.clip(np.rint(rgb), 0, 255)
    out[..., 3] = np.clip(np.rint(new_alpha * 255.0), 0, 255)
    return out


def _imprint_asset(
    asset: np.ndarray,
    scene_region: np.ndarray,
    scene_alpha_region: np.ndarray,
    light_dir: tuple[float, float] | None,
    spec: HarmonizeSpec,
) -> np.ndarray:
    """자산을 표면에 '새긴' 것처럼 변조 — 오려붙임이 아니라 인쇄·각인의 물리를 흉내낸다.

    - 질감 투과: 장면 휘도(그레인+음영)를 기준 휘도 대비 비율로 만들어 로고 색에
      선형 곱셈한다. 가죽 주름·하이라이트가 로고를 그대로 통과해 보인다. 곱셈이라
      hue는 돌지 않는다(브랜드 색 유지 — 밝기만 표면을 따른다).
    - 데보스 음영: 알파 경계 기울기와 광원 방향의 내적으로 눌린 자국의 명암을 만든다
      (광원 쪽 안벽은 그늘, 반대쪽 입술은 캐치라이트).
    - 잉크 불투명도: 알파를 살짝 낮춰 합성 시 표면 톤이 스며 보이게 한다.
    """
    alpha = asset[..., 3] / 255.0
    luma = _luma(scene_region)
    on_surface = scene_alpha_region > 0.5
    reference = float(luma[on_surface].mean()) if on_surface.any() else float(luma.mean())
    ratio = np.clip((luma + 1.0) / (reference + 1.0), 0.35, 1.8) ** (
        spec.imprint_texture * spec.strength
    )

    out = asset.copy()
    linear = np.power(out[..., :3] / 255.0, _GAMMA) * ratio[..., None]

    if light_dir is not None and spec.imprint_emboss > 0:
        edge = gaussian_filter(alpha, 2.0, mode="nearest")
        gy, gx = np.gradient(edge)
        ndotl = gx * light_dir[0] + gy * light_dir[1]
        emboss = np.clip(3.0 * spec.imprint_emboss * spec.strength * ndotl, -0.4, 0.4)
        linear *= (1.0 + emboss)[..., None]

    out[..., :3] = np.power(np.clip(linear, 0.0, 1.0), 1.0 / _GAMMA) * 255.0
    out[..., 3] = alpha * 255.0 * (1.0 - (1.0 - spec.ink_opacity) * spec.strength)
    return out


def _grain_stats(rgb: np.ndarray, mask: np.ndarray) -> float:
    """고주파 잔차의 강건 표준편차(MAD 기반).

    std는 반사 경계·윤곽선 같은 구조적 엣지(희소 이상치)에 끌려 그레인을 크게
    과대평가한다 — 크롬처럼 매끈하지만 엣지가 많은 장면에서 자산이 모래처럼 변한다.
    MAD×1.4826은 정규 노이즈에서 std와 일치하면서 이상치에 강건하다.
    """
    if not mask.any():
        return 0.0
    luma = _luma(rgb.astype(np.float64))
    residual = luma - gaussian_filter(luma, GRAIN_HP_SIGMA, mode="nearest")
    values = residual[mask]
    return float(1.4826 * np.median(np.abs(values - np.median(values))))


def _match_grain(
    asset: np.ndarray,
    scene_rgb: np.ndarray,
    ring: np.ndarray,
    spec: HarmonizeSpec,
    report: HarmonizeReport,
) -> np.ndarray:
    """링의 고주파 통계에 자산을 맞춘다 — 부족하면 노이즈 주입, 과하면 미세 블러."""
    scene_sigma = _grain_stats(scene_rgb, ring)
    solid = asset[..., 3] >= 200
    asset_sigma = _grain_stats(asset[..., :3], solid)
    out = asset.copy()

    if scene_sigma > asset_sigma + 0.5:
        amount = float(np.sqrt(scene_sigma**2 - asset_sigma**2)) * spec.strength
        rng = np.random.default_rng(spec.noise_seed)
        noise = rng.normal(0.0, amount, asset.shape[:2])
        present = asset[..., 3] >= 8
        rgb = out[..., :3].astype(np.float64) + noise[..., None] * present[..., None]
        out[..., :3] = np.clip(np.rint(rgb), 0, 255)
        report.grain_sigma = round(amount, 3)
    elif asset_sigma > 2.0 * max(scene_sigma, 0.5):
        # 자산이 장면보다 과하게 선명·거친 경우 — 문자 가독성 보존을 위해 σ≤1.0 상한
        sigma = min(1.0, 0.6 * spec.strength)
        rgb = out[..., :3].astype(np.float64)
        for c in range(3):
            rgb[..., c] = gaussian_filter(rgb[..., c], sigma, mode="nearest")
        out[..., :3] = np.clip(np.rint(rgb), 0, 255)
    return out


def harmonize(
    scene: Image.Image,
    asset_rgba: np.ndarray,
    placement: Placement,
    spec: HarmonizeSpec,
) -> tuple[Image.Image, np.ndarray, HarmonizeReport]:
    """(그림자가 합성된 scene, 조명·질감 정합된 asset, 리포트)를 반환한다."""
    report = HarmonizeReport()
    if spec.strength <= 0:
        report.skipped.append("strength=0")
        return scene, asset_rgba, report

    scene_arr = np.asarray(scene.convert("RGBA")).astype(np.float64)
    canvas_h, canvas_w = scene_arr.shape[:2]
    h, w = asset_rgba.shape[:2]
    x, y = placement.x, placement.y
    if x + w > canvas_w or y + h > canvas_h:
        raise PlacementOutOfBounds(
            f"자산이 캔버스를 벗어납니다: placement=({x},{y}) asset={w}x{h} "
            f"canvas={canvas_w}x{canvas_h}"
        )

    alpha_canvas = np.zeros((canvas_h, canvas_w), dtype=np.float64)
    alpha_canvas[y : y + h, x : x + w] = asset_rgba[..., 3] / 255.0
    presence_canvas = alpha_canvas >= (8 / 255.0)
    diag = float(np.hypot(w, h))

    # ① 조명장 — 링 표본이 부족하면 방향·리라이트를 신뢰하지 않는다
    sigma_l = max(8.0, 0.25 * diag)
    light, ring = estimate_light_field(scene_arr, presence_canvas, spec.ring_px, sigma_l)
    report.ring_pixels = int(ring.sum())
    light_dir = None
    ring_mean = 0.0
    if report.ring_pixels >= MIN_RING_PIXELS:
        light_dir = _light_direction(light, ring)
        report.light_dir = light_dir
        ring_mean = float(light[ring].mean())
    else:
        report.skipped.append(f"조명장: 링 표본 부족({report.ring_pixels}px)")

    if spec.mode == "imprint":
        # 새김은 표면의 일부다 — 그림자를 만들지 않고 질감·조명이 로고를 관통한다
        scene_out = scene_arr
        asset_out = _imprint_asset(
            asset_rgba.astype(np.float64),
            scene_arr[y : y + h, x : x + w, :3],
            scene_arr[y : y + h, x : x + w, 3] / 255.0,
            light_dir,
            spec,
        )
        report.skipped.append("imprint: 그림자·리라이트·그레인 생략(질감 투과로 대체)")
    else:
        # ② 그림자 — 접지는 등방이라 방향 없이도 유효하다
        scene_out = _apply_shadows(
            scene_arr, presence_canvas, alpha_canvas, light_dir, spec, diag, report
        )

        # ③ 리라이트
        asset_out = asset_rgba.astype(np.float64)
        if spec.relight > 0 and ring_mean > 1e-6:
            asset_out = _relight_asset(
                asset_out, light[y : y + h, x : x + w], ring_mean, spec
            )
            report.relight_applied = True
        elif spec.relight > 0:
            report.skipped.append("리라이트: 조명장 신뢰 불가")

    # ④ 라이트랩 + 페더 — 장면 색은 알파 가중 블러로 추출 (투명 검정 침출 방지)
    scene_alpha = scene_arr[..., 3] / 255.0
    scene_color = _normalized_blur(scene_arr[..., :3], scene_alpha, 4.0)
    asset_out = _edge_blend(
        asset_out, scene_color[y : y + h, x : x + w], spec, expand=spec.mode == "overlay"
    )

    # ⑤ 그레인·선예도 정합 (overlay 전용 — imprint는 질감이 이미 관통한다)
    asset_uint8 = np.clip(np.rint(asset_out), 0, 255).astype(np.uint8)
    if spec.mode == "overlay" and spec.grain_match and report.ring_pixels >= MIN_RING_PIXELS:
        asset_uint8 = _match_grain(asset_uint8, scene_arr[..., :3], ring, spec, report)
    elif spec.mode == "overlay" and spec.grain_match:
        report.skipped.append("그레인: 링 표본 부족")

    scene_img = Image.fromarray(
        np.clip(np.rint(scene_out), 0, 255).astype(np.uint8), "RGBA"
    )
    return scene_img, asset_uint8, report
