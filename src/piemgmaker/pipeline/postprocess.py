"""알파 후처리(결정론): 디프린지 → bbox 크롭 → 여백 정규화.

크롭은 자산 placement 좌표를 보정할 수 있도록 오프셋을 함께 반환한다
(paste-back이 크롭 이후에 오기 때문).
"""

from dataclasses import dataclass

import numpy as np
from PIL import Image

# opaque 경로에서 단색 배경 강제에 쓰는 중립 회색 — 프롬프트·디프린지·QA가 공유
FORCED_BG_COLOR = (128, 128, 128)


class ForegroundNotDetected(ValueError):
    pass


def estimate_bg_color(rgba: np.ndarray, patch: int = 16) -> tuple[int, int, int] | None:
    """코너 4곳 패치의 중앙값으로 실제 배경색을 추정한다.

    프롬프트로 배경색을 강제해도 실제 RGB는 표류하고, 오브젝트 고유색이
    고정 기준색과 겹치면 헤일로가 오탐된다.
    코너가 투명하면(네이티브 알파류) None을 돌려주고 호출부가 기본값을 쓴다.
    """
    height, width = rgba.shape[:2]
    size = max(2, min(patch, height // 4, width // 4))
    corners = [
        rgba[:size, :size],
        rgba[:size, -size:],
        rgba[-size:, :size],
        rgba[-size:, -size:],
    ]
    pixels = np.concatenate([c.reshape(-1, 4) for c in corners])
    opaque = pixels[pixels[:, 3] >= 200]
    if len(opaque) >= len(pixels) * 0.5:
        median = np.median(opaque[:, :3].astype(np.float64), axis=0)
        return tuple(int(v) for v in median)
    # 매팅 후 RGBA(코너 투명)는 저알파 프린지 픽셀이 배경색을 지배적으로 담고 있다
    alpha = rgba[..., 3]
    fringe = (alpha >= 8) & (alpha <= 96)
    if int(fringe.sum()) >= 200:
        median = np.median(rgba[fringe][:, :3].astype(np.float64), axis=0)
        return tuple(int(v) for v in median)
    return None


@dataclass(frozen=True)
class PostprocessSpec:
    # None이면 디프린지 생략 (native alpha 등 배경색이 정의되지 않는 경로)
    defringe_bg: tuple[int, int, int] | None = FORCED_BG_COLOR
    bbox_alpha_threshold: int = 8
    margin_ratio: float = 0.05
    # 경계 밴드에서 배경색 고스트(색≈배경인 반투명 잔여 픽셀) 제거 — "회색 테두리" 개선
    ghost_band_px: int = 3
    ghost_alpha_max: int = 200
    ghost_color_delta: float = 28.0


def suppress_bg_ghost(rgba: np.ndarray, bg: tuple[int, int, int], spec: "PostprocessSpec") -> np.ndarray:
    """경계 밴드의 배경색 고스트 픽셀 알파를 0으로 만든다.

    매팅이 배경 픽셀을 반투명으로 남기면 체커보드 위에서 옅은 테두리로 보인다.
    디프린지는 색만 복원하므로(순수 배경 픽셀은 배경색 그대로) 알파를 지워야 한다.
    색이 배경과 충분히 다른 soft alpha(유리·머리카락류)는 건드리지 않는다.
    """
    from piemgmaker.pipeline._morph import binary_dilate, binary_erode

    alpha = rgba[..., 3]
    presence = alpha >= spec.bbox_alpha_threshold
    if not presence.any():
        return rgba
    band = binary_dilate(presence, spec.ghost_band_px) & ~binary_erode(
        presence, spec.ghost_band_px
    )
    rgb = rgba[..., :3].astype(np.float64)
    color_dist = np.sqrt(((rgb - np.asarray(bg, dtype=np.float64)) ** 2).sum(axis=-1))
    ghost = band & (alpha < spec.ghost_alpha_max) & (color_dist < spec.ghost_color_delta)
    out = rgba.copy()
    out[..., 3][ghost] = 0
    return out


def defringe(rgba: np.ndarray, bg: tuple[int, int, int]) -> np.ndarray:
    """알려진 배경색 성분 제거: observed = a·fg + (1-a)·bg 에서 fg 복원."""
    arr = rgba.astype(np.float64)
    alpha = arr[..., 3:4] / 255.0
    bg_arr = np.asarray(bg, dtype=np.float64)
    fg = np.where(
        alpha > 0,
        (arr[..., :3] - (1.0 - alpha) * bg_arr) / np.maximum(alpha, 1e-6),
        0.0,
    )
    out = rgba.copy()
    out[..., :3] = np.clip(np.rint(fg), 0, 255).astype(np.uint8)
    return out


def crop_bbox(
    rgba: np.ndarray, alpha_threshold: int, margin_ratio: float
) -> tuple[np.ndarray, tuple[int, int]]:
    """알파 bbox로 크롭 후 투명 여백 패딩. 반환 오프셋 = (dx, dy), 새 좌표 = 원 좌표 + 오프셋."""
    mask = rgba[..., 3] >= alpha_threshold
    if not mask.any():
        raise ForegroundNotDetected("전경 미검출 — 알파가 전부 임계값 미만입니다")
    ys, xs = np.nonzero(mask)
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    cropped = rgba[y0:y1, x0:x1]
    margin = int(round(margin_ratio * max(cropped.shape[0], cropped.shape[1])))
    padded = np.pad(cropped, ((margin, margin), (margin, margin), (0, 0)))
    return padded, (margin - x0, margin - y0)


def postprocess(img: Image.Image, spec: PostprocessSpec) -> tuple[Image.Image, tuple[int, int]]:
    rgba = np.asarray(img.convert("RGBA"))
    if spec.defringe_bg is not None:
        rgba = suppress_bg_ghost(rgba, spec.defringe_bg, spec)
        rgba = defringe(rgba, spec.defringe_bg)
    rgba, offset = crop_bbox(rgba, spec.bbox_alpha_threshold, spec.margin_ratio)
    return Image.fromarray(rgba, "RGBA"), offset
