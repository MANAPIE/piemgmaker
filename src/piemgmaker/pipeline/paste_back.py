"""자산 paste-back 종단 + 코어 해시 검증 (코어 픽셀 100% 보존 요건).

- 코어 = 자산의 완전 불투명 영역을 밴드 폭만큼 침식한 부분. 픽셀 정확 복사한다.
- 검증은 지각 해시가 아니라 sha256 정확 비교 — 근사 통과를 허용하지 않는다.
- 2단 마스크(코어 0 / 전이 밴드 램프 / 외부 255)는 인페인팅 워크플로우 입력이다.
"""

import hashlib

import numpy as np
from PIL import Image

from piemgmaker.pipeline._morph import binary_dilate, binary_erode
from piemgmaker.schemas.generation import Placement

CORE_BAND_PX = 8  # 코어 침식·전이 밴드 폭


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


def paste_back(gen: Image.Image, asset_rgba: np.ndarray, placement: Placement) -> Image.Image:
    canvas = np.asarray(gen.convert("RGBA")).copy()
    x, y = _region(canvas, asset_rgba, placement)
    h, w = asset_rgba.shape[:2]

    overlay = np.zeros_like(canvas)
    overlay[y : y + h, x : x + w] = asset_rgba
    composited = np.asarray(
        Image.alpha_composite(Image.fromarray(canvas, "RGBA"), Image.fromarray(overlay, "RGBA"))
    ).copy()

    # 합성 반올림 오차와 무관하게 코어는 원본 픽셀 그대로 — 해시 100% 요건의 근거
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
) -> Image.Image:
    """2단 마스크: 코어 0(보호) / 전이 밴드 선형 램프 / 외부 255(재페인트). L 모드."""
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
    mask[core_canvas] = 0
    grown = core_canvas
    for step in range(1, band_px + 1):
        next_grown = binary_dilate(grown, 1)
        ring = next_grown & ~grown
        mask[ring] = round(255 * step / (band_px + 1))
        grown = next_grown
    return Image.fromarray(mask, "L")
