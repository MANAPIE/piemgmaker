"""골든 세트 러너 — RGB SSIM + 알파 이진화 IoU + soft alpha MAE.

임계값은 config가 아니라 케이스 파일에 명시한다.
"""

from pathlib import Path

import numpy as np
import yaml
from PIL import Image
from pydantic import BaseModel, Field
from skimage.metrics import structural_similarity

# a=0 영역의 RGB가 정의되지 않으므로 중립 회색 위에 합성한 뒤 SSIM을 계산한다
NEUTRAL_COMPOSITE_BG = 128
ALPHA_BINARIZE_THRESHOLD = 128


class GoldenThresholds(BaseModel):
    ssim_min: float = Field(ge=0.0, le=1.0)
    iou_min: float = Field(ge=0.0, le=1.0)
    mae_max: float = Field(ge=0.0, le=1.0)


class GoldenCase(BaseModel):
    id: str
    expected: str  # 케이스 파일 위치 기준 상대 경로
    thresholds: GoldenThresholds


class CaseResult(BaseModel):
    id: str
    passed: bool
    ssim: float | None = None
    iou: float | None = None
    mae: float | None = None
    failures: list[str] = Field(default_factory=list)


def composite_on_gray(rgba: np.ndarray) -> np.ndarray:
    alpha = rgba[..., 3:4].astype(np.float64) / 255.0
    rgb = rgba[..., :3].astype(np.float64)
    return rgb * alpha + NEUTRAL_COMPOSITE_BG * (1.0 - alpha)


def rgb_ssim(a: np.ndarray, b: np.ndarray) -> float:
    return float(
        structural_similarity(
            composite_on_gray(a), composite_on_gray(b), channel_axis=2, data_range=255.0
        )
    )


def alpha_iou(a: np.ndarray, b: np.ndarray, threshold: int = ALPHA_BINARIZE_THRESHOLD) -> float:
    mask_a = a[..., 3] >= threshold
    mask_b = b[..., 3] >= threshold
    union = int((mask_a | mask_b).sum())
    if union == 0:
        return 1.0
    return float((mask_a & mask_b).sum()) / union


def alpha_mae(a: np.ndarray, b: np.ndarray) -> float:
    return float(
        np.abs(a[..., 3].astype(np.float64) - b[..., 3].astype(np.float64)).mean() / 255.0
    )


def compare_images(
    case_id: str, produced: Path, expected: Path, thresholds: GoldenThresholds
) -> CaseResult:
    a = np.asarray(Image.open(produced).convert("RGBA"))
    b = np.asarray(Image.open(expected).convert("RGBA"))
    if a.shape != b.shape:
        return CaseResult(
            id=case_id, passed=False, failures=[f"크기 불일치: {a.shape[:2]} vs {b.shape[:2]}"]
        )
    ssim = rgb_ssim(a, b)
    iou = alpha_iou(a, b)
    mae = alpha_mae(a, b)
    failures = []
    if ssim < thresholds.ssim_min:
        failures.append(f"SSIM {ssim:.4f} < {thresholds.ssim_min}")
    if iou < thresholds.iou_min:
        failures.append(f"알파 IoU {iou:.4f} < {thresholds.iou_min}")
    if mae > thresholds.mae_max:
        failures.append(f"알파 MAE {mae:.4f} > {thresholds.mae_max}")
    return CaseResult(
        id=case_id,
        passed=not failures,
        ssim=round(ssim, 4),
        iou=round(iou, 4),
        mae=round(mae, 4),
        failures=failures,
    )


def load_cases(cases_dir: Path) -> list[tuple[GoldenCase, Path]]:
    cases: list[tuple[GoldenCase, Path]] = []
    for path in sorted(cases_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        cases.append((GoldenCase.model_validate(data), path.parent))
    return cases


def run_cases(cases_dir: Path, produced_root: Path) -> list[CaseResult]:
    """산출물은 produced_root/<case-id>.png 규약으로 찾는다."""
    results: list[CaseResult] = []
    for case, base_dir in load_cases(cases_dir):
        produced = produced_root / f"{case.id}.png"
        expected = base_dir / case.expected
        if not produced.is_file():
            results.append(
                CaseResult(id=case.id, passed=False, failures=[f"산출물 없음: {produced}"])
            )
            continue
        if not expected.is_file():
            results.append(
                CaseResult(id=case.id, passed=False, failures=[f"기준 이미지 없음: {expected}"])
            )
            continue
        results.append(compare_images(case.id, produced, expected, case.thresholds))
    return results
