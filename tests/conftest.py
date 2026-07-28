import numpy as np
import pytest
from PIL import Image


def make_rgba(width: int, height: int, color=(0, 0, 0, 0)) -> np.ndarray:
    arr = np.zeros((height, width, 4), dtype=np.uint8)
    arr[:, :] = color
    return arr


def to_image(arr: np.ndarray) -> Image.Image:
    return Image.fromarray(arr, "RGBA")


@pytest.fixture
def opaque_asset() -> np.ndarray:
    """40x40 완전 불투명 파랑 — 코어 해시 테스트용 (코어 = 8px 침식 후 24x24)."""
    return make_rgba(40, 40, (20, 40, 200, 255))


@pytest.fixture
def blob_image() -> Image.Image:
    """128x128 투명 캔버스 중앙에 60x60 불투명 빨강 — QA·후처리 공용 픽스처."""
    arr = make_rgba(128, 128)
    arr[34:94, 34:94] = (200, 30, 30, 255)
    return to_image(arr)
