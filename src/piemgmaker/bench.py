"""배경 제거 3트랙 비교 하니스.

지표: 경계 품질(boundary MAE) · soft alpha 충실도(alpha MAE) · 색 오염 · 속도.
전략 러너는 실 모델을 실행하고, 지표 계산은 결정론 구간이다.
"""

import time
from collections.abc import Callable
from pathlib import Path

import numpy as np
import yaml
from PIL import Image
from pydantic import BaseModel, Field

from piemgmaker.golden.runner import ALPHA_BINARIZE_THRESHOLD, alpha_mae
from piemgmaker.pipeline._morph import binary_dilate, binary_erode
from piemgmaker.pipeline.qa import QAThresholds, check_halo
from piemgmaker.schemas.style_pack import MaterialClass

# 러너: 입력 RGB 경로 → RGBA 결과. 소요 시간은 하니스가 계측한다.
StrategyRunner = Callable[[Path], Image.Image]


class BenchItem(BaseModel):
    id: str
    rgb: str  # 세트 파일 위치 기준 상대 경로
    gt_alpha: str | None = None  # 기준 알파(L PNG). 없으면 MAE류 지표 생략
    material: MaterialClass


class BenchSet(BaseModel):
    items: list[BenchItem] = Field(min_length=1)


class BenchRow(BaseModel):
    strategy: str
    item_id: str
    material: MaterialClass
    seconds: float
    alpha_mae: float | None = None
    boundary_mae: float | None = None
    contamination: float | None = None


def load_bench_set(path: Path) -> tuple[BenchSet, Path]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return BenchSet.model_validate(data), path.parent


def validate_set(bench_set: BenchSet, base_dir: Path) -> list[str]:
    problems: list[str] = []
    for item in bench_set.items:
        if not (base_dir / item.rgb).is_file():
            problems.append(f"{item.id}: 입력 없음 {item.rgb}")
        if item.gt_alpha and not (base_dir / item.gt_alpha).is_file():
            problems.append(f"{item.id}: 기준 알파 없음 {item.gt_alpha}")
    return problems


def boundary_mae(pred_alpha: np.ndarray, gt_alpha: np.ndarray, band_px: int = 4) -> float:
    """기준 알파 경계 밴드에서의 MAE — 경계 품질 지표."""
    gt_mask = gt_alpha >= ALPHA_BINARIZE_THRESHOLD
    band = binary_dilate(gt_mask, band_px) & ~binary_erode(gt_mask, band_px)
    if not band.any():
        return 0.0
    diff = np.abs(pred_alpha.astype(np.float64) - gt_alpha.astype(np.float64))
    return float(diff[band].mean() / 255.0)


def contamination_score(rgba: np.ndarray, material: MaterialClass) -> float:
    check = check_halo(rgba, material, QAThresholds())
    return check.measured or 0.0


def run_bench(
    bench_set: BenchSet,
    base_dir: Path,
    runners: dict[str, StrategyRunner],
    out_md: Path,
) -> list[BenchRow]:
    rows: list[BenchRow] = []
    for strategy, runner in runners.items():
        for item in bench_set.items:
            started = time.perf_counter()
            result = runner(base_dir / item.rgb)
            seconds = time.perf_counter() - started
            pred = np.asarray(result.convert("RGBA"))
            row = BenchRow(
                strategy=strategy,
                item_id=item.id,
                material=item.material,
                seconds=round(seconds, 3),
                contamination=round(contamination_score(pred, item.material), 4),
            )
            if item.gt_alpha:
                gt = np.asarray(Image.open(base_dir / item.gt_alpha).convert("L"))
                pred_rgba_alpha = pred[..., 3]
                gt_rgba = np.dstack([np.zeros((*gt.shape, 3), np.uint8), gt])
                pred_stack = np.dstack([np.zeros((*pred_rgba_alpha.shape, 3), np.uint8), pred_rgba_alpha])
                row.alpha_mae = round(alpha_mae(pred_stack, gt_rgba), 4)
                row.boundary_mae = round(boundary_mae(pred_rgba_alpha, gt, band_px=4), 4)
            rows.append(row)
    write_report(rows, out_md)
    return rows


def write_report(rows: list[BenchRow], out_md: Path) -> None:
    lines = [
        "# 배경 제거 3트랙 비교",
        "",
        "| 전략 | 케이스 | material | alpha MAE | boundary MAE | 색 오염 | 속도(s) |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        fmt = lambda v: "-" if v is None else f"{v}"
        lines.append(
            f"| {r.strategy} | {r.item_id} | {r.material} | {fmt(r.alpha_mae)} "
            f"| {fmt(r.boundary_mae)} | {fmt(r.contamination)} | {r.seconds} |"
        )
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
