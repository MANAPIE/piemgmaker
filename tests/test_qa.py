import numpy as np

from piemgmaker.pipeline.qa import (
    QAThresholds,
    check_coverage,
    check_edge_contact,
    check_halo,
    run_qa,
)
from tests.conftest import make_rgba

THR = QAThresholds()


def blob_alpha(size=128, lo=34, hi=94) -> np.ndarray:
    alpha = np.zeros((size, size), dtype=np.uint8)
    alpha[lo:hi, lo:hi] = 255
    return alpha


def test_경계에_접촉하면_잘림_실패와_재생성_힌트를_준다():
    touching = blob_alpha(lo=0, hi=60)
    check = check_edge_contact(touching, THR)
    assert not check.passed
    assert check.regen_hint

    clean = check_edge_contact(blob_alpha(), THR)
    assert clean.passed and clean.regen_hint is None


def test_커버리지는_15에서_80퍼센트_범위만_통과한다():
    assert check_coverage(blob_alpha(), THR).passed  # ~22%
    tiny = blob_alpha(lo=60, hi=70)  # ~0.6%
    low = check_coverage(tiny, THR)
    assert not low.passed and "작습니다" in low.regen_hint
    full = blob_alpha(lo=2, hi=126)  # ~94%
    high = check_coverage(full, THR)
    assert not high.passed and "과점" in high.regen_hint


def _rimmed_blob() -> np.ndarray:
    """빨강 블롭의 최외곽 1px을 배경색으로 오염시킨 이미지 — 헤일로 시뮬레이션."""
    arr = make_rgba(128, 128)
    arr[34:94, 34:94] = (200, 30, 30, 255)
    rim = np.zeros((128, 128), dtype=bool)
    rim[34:94, 34:94] = True
    rim[35:93, 35:93] = False
    arr[rim] = (128, 128, 128, 255)
    return arr


def test_헤일로는_opaque에서_실패하고_translucent에서는_완화된다():
    rimmed = _rimmed_blob()
    opaque = check_halo(rimmed, "opaque", THR)
    assert not opaque.passed
    translucent = check_halo(rimmed, "translucent", THR)
    assert translucent.passed  # soft alpha 오탐 방지 — 동일 이미지, 완화 한도


def test_깨끗한_블롭은_헤일로_검사를_통과한다():
    arr = make_rgba(128, 128)
    arr[34:94, 34:94] = (200, 30, 30, 255)
    assert check_halo(arr, "opaque", THR).passed


def test_run_qa는_세_룰을_모두_보고한다(blob_image):
    canvas_alpha = np.asarray(blob_image)[..., 3]
    checks = run_qa(blob_image, canvas_alpha, "opaque", THR)
    assert [c.rule for c in checks] == ["edge-contact", "coverage", "halo"]
    assert all(c.passed for c in checks)
