import numpy as np
import pytest
from PIL import Image

from piemgmaker.pipeline.postprocess import (
    ForegroundNotDetected,
    PostprocessSpec,
    crop_bbox,
    defringe,
    postprocess,
)
from tests.conftest import make_rgba, to_image


def test_디프린지는_알려진_배경색_성분을_제거한다():
    bg = (128, 128, 128)
    alpha = 128
    a = alpha / 255.0
    observed_r = round(a * 200 + (1 - a) * bg[0])
    observed_g = round(a * 0 + (1 - a) * bg[1])
    arr = make_rgba(4, 4, (observed_r, observed_g, observed_g, alpha))
    out = defringe(arr, bg)
    assert abs(int(out[0, 0, 0]) - 200) <= 2
    assert abs(int(out[0, 0, 1]) - 0) <= 2
    assert out[0, 0, 3] == alpha  # 알파는 보존


def test_크롭은_오프셋을_반환해_자산_좌표_보정을_가능하게_한다():
    arr = make_rgba(100, 80)
    arr[20:40, 10:30] = (255, 0, 0, 255)  # 20x20 오브젝트
    cropped, (dx, dy) = crop_bbox(arr, alpha_threshold=8, margin_ratio=0.1)
    margin = 2  # round(0.1 * 20)
    assert cropped.shape == (20 + 2 * margin, 20 + 2 * margin, 4)
    assert (dx, dy) == (margin - 10, margin - 20)
    # 원 좌표 (10,20) + 오프셋 = (margin, margin)에 오브젝트가 있어야 한다
    assert tuple(cropped[margin, margin][:3]) == (255, 0, 0)


def test_배경색은_코너_중앙값으로_추정된다():
    from piemgmaker.pipeline.postprocess import estimate_bg_color

    arr = make_rgba(128, 128, (172, 168, 180, 255))  # 실측 표류 범위의 밝은 배경
    arr[40:90, 40:90] = (200, 30, 30, 255)
    assert estimate_bg_color(arr) == (172, 168, 180)


def test_코너가_투명하면_배경색_추정은_None이다():
    from piemgmaker.pipeline.postprocess import estimate_bg_color

    arr = make_rgba(64, 64)  # native alpha류 — 배경 없음
    arr[16:48, 16:48] = (10, 200, 10, 255)
    assert estimate_bg_color(arr) is None


def test_매팅_후_이미지는_저알파_프린지에서_배경색을_추정한다():
    from piemgmaker.pipeline.postprocess import estimate_bg_color

    arr = make_rgba(128, 128)  # 코너 투명 (매팅 후)
    arr[30:98, 30:98] = (200, 30, 30, 255)
    # 경계에 배경색(170,170,170) 저알파 프린지 링
    arr[28:30, 28:100] = (170, 170, 170, 40)
    arr[98:100, 28:100] = (170, 170, 170, 40)
    arr[30:98, 28:30] = (170, 170, 170, 40)
    arr[30:98, 98:100] = (170, 170, 170, 40)
    assert estimate_bg_color(arr) == (170, 170, 170)


def test_배경색_고스트_링은_알파에서_제거되고_soft_alpha는_보존된다():
    from piemgmaker.pipeline.postprocess import PostprocessSpec, suppress_bg_ghost

    bg = (170, 170, 170)
    arr = make_rgba(128, 128)
    arr[30:98, 30:98] = (200, 30, 30, 255)
    arr[28:30, 28:100] = (*bg, 120)  # 배경색 고스트 (위쪽 링)
    arr[98:100, 28:100] = (60, 180, 220, 120)  # 색이 다른 진짜 soft alpha (아래쪽)
    out = suppress_bg_ghost(arr, bg, PostprocessSpec(defringe_bg=bg))
    assert out[28, 60, 3] == 0  # 고스트 제거
    assert out[98, 60, 3] == 120  # soft alpha 보존
    assert out[60, 60, 3] == 255  # 오브젝트 본체 보존


def test_전경이_없으면_ForegroundNotDetected다():
    with pytest.raises(ForegroundNotDetected):
        crop_bbox(make_rgba(32, 32), alpha_threshold=8, margin_ratio=0.05)


def test_postprocess는_동일_입력에_동일_출력이다(blob_image):
    spec = PostprocessSpec()
    out1, off1 = postprocess(blob_image, spec)
    out2, off2 = postprocess(blob_image, spec)
    assert off1 == off2
    assert np.array_equal(np.asarray(out1), np.asarray(out2))


def test_native_alpha_경로는_디프린지를_생략한다():
    arr = make_rgba(64, 64)
    arr[16:48, 16:48] = (10, 200, 10, 200)
    img = to_image(arr)
    out, _ = postprocess(img, PostprocessSpec(defringe_bg=None, margin_ratio=0.0))
    assert tuple(np.asarray(out)[0, 0]) == (10, 200, 10, 200)  # 색 무변형
