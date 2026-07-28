import numpy as np
import yaml
from PIL import Image

from piemgmaker.golden.runner import (
    GoldenThresholds,
    alpha_iou,
    alpha_mae,
    compare_images,
    rgb_ssim,
    run_cases,
)
from tests.conftest import make_rgba

THRESHOLDS = GoldenThresholds(ssim_min=0.95, iou_min=0.9, mae_max=0.05)


def blob(color=(200, 30, 30, 255), lo=8, hi=56) -> np.ndarray:
    arr = make_rgba(64, 64)
    arr[lo:hi, lo:hi] = color
    return arr


def test_동일_이미지는_만점이다():
    a = blob()
    assert rgb_ssim(a, a) == 1.0
    assert alpha_iou(a, a) == 1.0
    assert alpha_mae(a, a) == 0.0


def test_알파_영역이_어긋나면_IoU가_떨어진다():
    iou = alpha_iou(blob(), blob(lo=16, hi=64))
    assert iou < 1.0


def test_soft_alpha_차이는_MAE로_잡힌다():
    soft = blob(color=(200, 30, 30, 128))
    assert alpha_mae(blob(), soft) > 0.1


def test_둘_다_빈_알파면_IoU는_1이다():
    empty = make_rgba(64, 64)
    assert alpha_iou(empty, empty) == 1.0


def test_크기_불일치는_지표_없이_실패한다(tmp_path):
    a_path, b_path = tmp_path / "a.png", tmp_path / "b.png"
    Image.fromarray(blob(), "RGBA").save(a_path)
    Image.fromarray(make_rgba(32, 32), "RGBA").save(b_path)
    result = compare_images("case", a_path, b_path, THRESHOLDS)
    assert not result.passed
    assert "크기 불일치" in result.failures[0]


def test_run_cases는_케이스별_판정과_누락을_보고한다(tmp_path):
    cases_dir = tmp_path / "cases"
    expected_dir = tmp_path / "expected"
    produced_dir = tmp_path / "produced"
    for d in (cases_dir, expected_dir, produced_dir):
        d.mkdir()

    Image.fromarray(blob(), "RGBA").save(expected_dir / "ok.png")
    Image.fromarray(blob(), "RGBA").save(produced_dir / "ok.png")
    for case_id in ("ok", "missing"):
        (cases_dir / f"{case_id}.yaml").write_text(
            yaml.safe_dump(
                {
                    "id": case_id,
                    "expected": f"../expected/{case_id}.png",
                    "thresholds": THRESHOLDS.model_dump(),
                }
            ),
            encoding="utf-8",
        )
    Image.fromarray(blob(), "RGBA").save(expected_dir / "missing.png")

    results = {r.id: r for r in run_cases(cases_dir, produced_dir)}
    assert results["ok"].passed
    assert not results["missing"].passed
    assert "산출물 없음" in results["missing"].failures[0]
