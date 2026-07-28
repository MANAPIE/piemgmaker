"""이진 모폴로지(8-이웃) — 외부 의존성 API 변동을 피하려고 numpy로 직접 구현. 결정론."""

import numpy as np


def _erode1(mask: np.ndarray) -> np.ndarray:
    padded = np.pad(mask, 1, constant_values=False)
    out = np.ones_like(mask)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            h, w = mask.shape
            out &= padded[1 + dy : 1 + dy + h, 1 + dx : 1 + dx + w]
    return out


def _dilate1(mask: np.ndarray) -> np.ndarray:
    padded = np.pad(mask, 1, constant_values=False)
    out = np.zeros_like(mask)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            h, w = mask.shape
            out |= padded[1 + dy : 1 + dy + h, 1 + dx : 1 + dx + w]
    return out


def binary_erode(mask: np.ndarray, radius: int) -> np.ndarray:
    out = mask.astype(bool).copy()
    for _ in range(radius):
        out = _erode1(out)
    return out


def binary_dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    out = mask.astype(bool).copy()
    for _ in range(radius):
        out = _dilate1(out)
    return out
