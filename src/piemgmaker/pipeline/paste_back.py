"""자산 paste-back 종단 + 코어 보존 검증.

- 코어 = 자산의 완전 불투명 영역을 밴드 폭만큼 침식한 부분.
- core_policy="force"(기본)는 코어를 픽셀 정확 복사하고 sha256 정확 비교로 검증한다.
- core_policy="blend"는 하모나이즈된 자산의 합성 결과를 그대로 두고,
  검증은 해시가 아니라 편차 상한(measure_asset_fidelity)으로 한다.
- 마스크(코어 core_value / 전이 밴드 램프 / 외부 255)는 인페인팅 워크플로우 입력이다.
"""

import hashlib
from dataclasses import dataclass
from typing import Literal

import numpy as np
from PIL import Image

from piemgmaker.pipeline._morph import binary_dilate, binary_erode
from piemgmaker.schemas.generation import Corner, Placement
from piemgmaker.schemas.style_pack import FidelitySpec, MaskRamp, TiltPreset

CORE_BAND_PX = 8  # 코어 침식·전이 밴드 폭

CorePolicy = Literal["force", "blend"]

# 틸트 프리셋 — 출력 사각형의 정규화 목적 코너(TL·TR·BR·BL). 캔버스·자산 크기와
# 무관한 비율이라 결정론이 유지된다. 값은 시각 프리셋으로, 팩 요구에 맞춰 조정 가능.
TILT_QUADS: dict[str, tuple[Corner, Corner, Corner, Corner]] = {
    "slight": ((0.05, 0.0), (0.95, 0.0), (1.0, 1.0), (0.0, 1.0)),
    "iso": ((0.18, 0.0), (1.0, 0.14), (0.82, 1.0), (0.0, 0.86)),
    "strong": ((0.14, 0.0), (0.86, 0.0), (1.0, 1.0), (0.0, 1.0)),
}


class AssetCoreEmpty(ValueError):
    pass


class PlacementOutOfBounds(ValueError):
    pass


def scale_asset(asset: Image.Image, scale: float) -> np.ndarray:
    """배치 스케일 적용. 검증도 동일 리샘플 결과와 비교하므로 필터는 LANCZOS로 고정."""
    rgba = asset.convert("RGBA")
    if scale != 1.0:
        width = max(1, round(rgba.width * scale))
        height = max(1, round(rgba.height * scale))
        rgba = rgba.resize((width, height), Image.LANCZOS)
    return np.asarray(rgba)


def _perspective_coeffs(
    src: list[tuple[float, float]], dst: list[tuple[float, float]]
) -> list[float]:
    """PIL PERSPECTIVE 계수 — 출력 좌표(dst)를 입력 좌표(src)로 사상하는 8계수."""
    rows = []
    rhs = []
    for (sx, sy), (dx, dy) in zip(src, dst):
        rows.append([dx, dy, 1, 0, 0, 0, -sx * dx, -sx * dy])
        rows.append([0, 0, 0, dx, dy, 1, -sy * dx, -sy * dy])
        rhs.extend([sx, sy])
    return list(np.linalg.solve(np.asarray(rows, dtype=np.float64), np.asarray(rhs, dtype=np.float64)))


def _crop_alpha_bbox(rgba: np.ndarray) -> np.ndarray:
    ys, xs = np.nonzero(rgba[..., 3] > 0)
    if len(ys) == 0:
        return rgba
    return rgba[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]


def transform_asset(
    asset: Image.Image,
    scale: float,
    rotation_deg: float = 0.0,
    tilt: TiltPreset = "none",
) -> tuple[np.ndarray, tuple[Corner, Corner, Corner, Corner] | None]:
    """회전 → 원근 → 스케일 순의 결정론 변환. (RGBA, 사용한 정규화 사변형)을 반환한다.

    스케일을 마지막에 적용해야 캔버스 맞춤 스케일이 변환 후 크기에 정확히 맞는다
    (회전·원근이 bbox를 키우므로 스케일을 먼저 하면 맞춤이 어긋난다).
    """
    rgba = asset.convert("RGBA")
    if rotation_deg:
        rgba = rgba.rotate(rotation_deg, expand=True, resample=Image.BICUBIC)
    quad = TILT_QUADS.get(tilt)
    if quad is not None:
        w, h = rgba.width, rgba.height
        src = [(0.0, 0.0), (w - 1.0, 0.0), (w - 1.0, h - 1.0), (0.0, h - 1.0)]
        dst = [(nx * (w - 1), ny * (h - 1)) for nx, ny in quad]
        coeffs = _perspective_coeffs(src, dst)
        rgba = rgba.transform((w, h), Image.PERSPECTIVE, coeffs, resample=Image.BICUBIC)
    arr = np.asarray(rgba)
    if rotation_deg or quad is not None:
        arr = _crop_alpha_bbox(arr)
    if scale != 1.0:
        arr = scale_asset(Image.fromarray(arr, "RGBA"), scale)
    return arr, quad


# 코어 판정용 불투명 임계 — 255뿐 아니라 내보내기 앤티앨리어싱으로 254가 된 픽셀도 코어로 본다.
# 코어는 합성 수학이 아니라 paste-back의 강제 복사로 보존되므로 보장은 동일하다.
CORE_SOLID_ALPHA = 250


def core_mask(asset_rgba: np.ndarray, band_px: int = CORE_BAND_PX) -> np.ndarray:
    """불투명 코어 — 침식 반경을 자산 형상에 맞춰 적응적으로 줄인다.

    두꺼운 자산은 band_px 침식으로 넉넉한 코어를 얻고, 가는 획 로고(워드마크)는
    반경을 줄여도 결정론이 유지된다(같은 입력 → 같은 반경 선택 → 같은 코어).
    """
    solid = asset_rgba[..., 3] >= CORE_SOLID_ALPHA
    for radius in (band_px, band_px // 2, 2, 1, 0):
        core = binary_erode(solid, radius) if radius > 0 else solid
        if core.any():
            return core
    raise AssetCoreEmpty(
        f"불투명(alpha≥{CORE_SOLID_ALPHA}) 픽셀이 전혀 없습니다 — "
        "로고·오브젝트를 완전 불투명으로 내보낸 뒤 등록하세요"
    )


def _region(canvas: np.ndarray, asset_rgba: np.ndarray, placement: Placement) -> tuple[int, int]:
    h, w = asset_rgba.shape[:2]
    if placement.x + w > canvas.shape[1] or placement.y + h > canvas.shape[0]:
        raise PlacementOutOfBounds(
            f"자산이 캔버스를 벗어납니다: placement=({placement.x},{placement.y}) "
            f"asset={w}x{h} canvas={canvas.shape[1]}x{canvas.shape[0]}"
        )
    return placement.x, placement.y


def paste_back(
    gen: Image.Image,
    asset_rgba: np.ndarray,
    placement: Placement,
    *,
    core_policy: CorePolicy = "force",
) -> Image.Image:
    canvas = np.asarray(gen.convert("RGBA")).copy()
    x, y = _region(canvas, asset_rgba, placement)
    h, w = asset_rgba.shape[:2]

    overlay = np.zeros_like(canvas)
    overlay[y : y + h, x : x + w] = asset_rgba
    composited = np.asarray(
        Image.alpha_composite(Image.fromarray(canvas, "RGBA"), Image.fromarray(overlay, "RGBA"))
    ).copy()

    if core_policy == "force":
        # 합성 반올림 오차와 무관하게 코어는 원본 픽셀 그대로 — 해시 100% 요건의 근거.
        # blend 정책은 이 강제 복사를 생략하고 편차 상한 검증으로 대체한다.
        core = core_mask(asset_rgba)
        region = composited[y : y + h, x : x + w]
        region[core] = asset_rgba[core]
        composited[y : y + h, x : x + w] = region
    return Image.fromarray(composited, "RGBA")


def _core_digest(pixels: np.ndarray, core: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(pixels[core]).tobytes()).hexdigest()


def verify_core_hash(
    result: Image.Image, asset_rgba: np.ndarray, placement: Placement
) -> bool:
    core = core_mask(asset_rgba)
    arr = np.asarray(result.convert("RGBA"))
    x, y = _region(arr, asset_rgba, placement)
    h, w = asset_rgba.shape[:2]
    region = arr[y : y + h, x : x + w]
    return _core_digest(region, core) == _core_digest(asset_rgba, core)


def make_inpaint_mask(
    canvas_size: tuple[int, int],
    asset_rgba: np.ndarray,
    placement: Placement,
    band_px: int = CORE_BAND_PX,
    core_value: int = 0,
    ramp: MaskRamp = "linear",
) -> Image.Image:
    """마스크: 코어 core_value(0=완전 보호) / 전이 밴드 램프 / 외부 255(재페인트). L 모드.

    core_value>0이면 모델이 코어를 저강도로 재통합한다(주변 반사·색 번짐의 일관성).
    코어의 브랜드 정확도는 이후 paste-back이 복원하므로 위험은 밴드·주변에 한정된다.
    """
    width, height = canvas_size
    x, y = placement.x, placement.y
    h, w = asset_rgba.shape[:2]
    if x + w > width or y + h > height:
        raise PlacementOutOfBounds(
            f"자산이 캔버스를 벗어납니다: placement=({x},{y}) asset={w}x{h} canvas={width}x{height}"
        )

    core_canvas = np.zeros((height, width), dtype=bool)
    core_canvas[y : y + h, x : x + w] = core_mask(asset_rgba, band_px)

    mask = np.full((height, width), 255, dtype=np.uint8)
    mask[core_canvas] = core_value
    grown = core_canvas
    for step in range(1, band_px + 1):
        next_grown = binary_dilate(grown, 1)
        ring = next_grown & ~grown
        t = step / (band_px + 1)
        if ramp == "smoothstep":
            t = t * t * (3.0 - 2.0 * t)
        # 램프는 core_value→255 구간을 보간해 코어보다 낮은 값이 나오지 않게 한다
        mask[ring] = round(core_value + (255 - core_value) * t)
        grown = next_grown
    return Image.fromarray(mask, "L")


@dataclass(frozen=True)
class FidelityReport:
    """blend 정책의 자산 편차 측정 — sha256 대신 형상 IoU·색차·휘도 변조를 상한 검사."""

    shape_iou: float
    mean_delta_e: float
    max_delta_e: float
    hue_shift_deg: float
    passed: bool


def measure_asset_fidelity(
    result: Image.Image,
    pasted_rgba: np.ndarray,
    original_rgba: np.ndarray,
    placement: Placement,
    spec: FidelitySpec | None = None,
) -> FidelityReport:
    """합성 결과의 코어 색 편차 + 하모나이즈된 자산의 형상 편차를 원본 대비 측정한다.

    - 형상은 합성 결과가 아니라 pasted_rgba(하모나이즈 완료 자산)의 알파로 잰다 —
      합성 후에는 뒤에 깔린 장면 알파가 겹쳐 자산 형상만 분리할 수 없다.
    - 색은 합성 결과의 코어 픽셀을 원본과 CIE76으로 비교한다. 코어는 원본 기준
      완전 불투명이라 합성 수학의 영향이 없고, 하모나이즈 변조량이 그대로 잡힌다.
    """
    from skimage.color import rgb2lab  # 무거운 임포트 — blend 경로에서만 지연 로드

    spec = spec or FidelitySpec()

    def solid(alpha: np.ndarray) -> np.ndarray:
        # 임계를 이미지별 최대 알파의 절반으로 정규화 — ink_opacity 같은 균일 스케일은
        # 형상을 보존하므로(고정 128이면 경계 AA 픽셀이 무더기 탈락) IoU가 왜곡되지 않는다
        return alpha >= max(1, int(alpha.max()) // 2)

    original_solid = solid(original_rgba[..., 3])
    pasted_solid = solid(pasted_rgba[..., 3])
    union = int((original_solid | pasted_solid).sum())
    inter = int((original_solid & pasted_solid).sum())
    shape_iou = inter / union if union else 0.0

    core = core_mask(original_rgba)
    arr = np.asarray(result.convert("RGBA"))
    x, y = _region(arr, original_rgba, placement)
    h, w = original_rgba.shape[:2]
    region = arr[y : y + h, x : x + w]

    lab_result = rgb2lab(region[core][:, :3].astype(np.float64) / 255.0)
    lab_original = rgb2lab(original_rgba[core][:, :3].astype(np.float64) / 255.0)
    delta = np.sqrt(((lab_result - lab_original) ** 2).sum(axis=-1))

    # 무채색 픽셀은 hue가 불안정하므로 채도(chroma) 하한을 둔다
    chroma_original = np.hypot(lab_original[:, 1], lab_original[:, 2])
    chroma_result = np.hypot(lab_result[:, 1], lab_result[:, 2])
    chromatic = (chroma_original > 10.0) & (chroma_result > 10.0)
    if chromatic.any():
        hue_original = np.degrees(np.arctan2(lab_original[chromatic, 2], lab_original[chromatic, 1]))
        hue_result = np.degrees(np.arctan2(lab_result[chromatic, 2], lab_result[chromatic, 1]))
        diff = (hue_result - hue_original + 180.0) % 360.0 - 180.0
        hue_shift = float(np.abs(diff).mean())
    else:
        hue_shift = 0.0

    mean_delta = float(delta.mean())
    passed = (
        shape_iou >= spec.shape_iou_min
        and mean_delta <= spec.mean_delta_e_max
        and hue_shift <= spec.hue_shift_max_deg
    )
    return FidelityReport(
        shape_iou=round(shape_iou, 4),
        mean_delta_e=round(mean_delta, 3),
        max_delta_e=round(float(delta.max()), 3),
        hue_shift_deg=round(hue_shift, 2),
        passed=passed,
    )
