"""알파 QA 하드 룰 — soft alpha를 오탐하지 않도록 material_class 인지.

잘림·커버리지는 크롭 전 원본 캔버스 알파로 판정해야 의미가 있다
(크롭 후에는 경계 접촉이 항상 사라진다). 헤일로는 최종 이미지에서 판정한다.
"""

from dataclasses import dataclass

import numpy as np
from PIL import Image

from piemgmaker.pipeline._morph import binary_dilate, binary_erode
from piemgmaker.pipeline.postprocess import FORCED_BG_COLOR
from piemgmaker.schemas.qa import QACheck
from piemgmaker.schemas.style_pack import MaterialClass

RULE_EDGE_CONTACT = "edge-contact"
RULE_COVERAGE = "coverage"
RULE_HALO = "halo"
RULE_CORE_HASH = "core-hash"  # 오케스트레이터가 paste-back 검증 결과를 이 룰로 기록
RULE_FOREGROUND = "foreground-detected"  # 전경 미검출(후처리 실패)도 QA 리포트에 기록


@dataclass(frozen=True)
class QAThresholds:
    """알파 QA 임계값 — 커버리지 하한·상한, 헤일로·전경 판정 기준."""

    presence_alpha: int = 16  # 이 이상이면 '전경 존재' 픽셀로 취급
    coverage_min: float = 0.12
    coverage_max: float = 0.80
    halo_alpha_min: float = 0.5
    halo_color_delta: float = 30.0
    halo_max_ratio: float = 0.05
    halo_max_ratio_translucent: float = 0.20  # soft alpha 오탐 방지 완화치
    boundary_band_px: int = 4


def check_edge_contact(canvas_alpha: np.ndarray, thresholds: QAThresholds) -> QACheck:
    border = np.concatenate(
        [canvas_alpha[0], canvas_alpha[-1], canvas_alpha[:, 0], canvas_alpha[:, -1]]
    )
    touching = int((border >= thresholds.presence_alpha).sum())
    return QACheck(
        rule=RULE_EDGE_CONTACT,
        passed=touching == 0,
        measured=float(touching),
        detail=f"경계 접촉 픽셀 {touching}개",
        regen_hint=(
            None
            if touching == 0
            else "오브젝트가 캔버스 경계에서 잘렸습니다 — 구도 축소·여백 확대 프롬프트로 재생성하세요"
        ),
    )


def check_coverage(canvas_alpha: np.ndarray, thresholds: QAThresholds) -> QACheck:
    coverage = float((canvas_alpha >= thresholds.presence_alpha).mean())
    passed = thresholds.coverage_min <= coverage <= thresholds.coverage_max
    hint = None
    if coverage < thresholds.coverage_min:
        hint = "오브젝트가 너무 작습니다 — 클로즈업·확대 구도로 재생성하세요"
    elif coverage > thresholds.coverage_max:
        hint = "오브젝트가 캔버스를 과점합니다 — 여백을 확보해 재생성하세요"
    return QACheck(
        rule=RULE_COVERAGE,
        passed=passed,
        measured=round(coverage, 4),
        detail=f"알파 커버리지 {coverage:.1%} (허용 {thresholds.coverage_min:.0%}~{thresholds.coverage_max:.0%})",
        regen_hint=hint,
    )


def check_halo(
    final_rgba: np.ndarray,
    material: MaterialClass,
    thresholds: QAThresholds,
    bg_color: tuple[int, int, int] = FORCED_BG_COLOR,
) -> QACheck:
    """경계 밴드에서 배경색에 가까운 고알파 픽셀 비율로 헤일로를 판정한다."""
    alpha = final_rgba[..., 3]
    presence = alpha >= thresholds.presence_alpha
    band = binary_dilate(presence, thresholds.boundary_band_px) & ~binary_erode(
        presence, thresholds.boundary_band_px
    )
    band_count = int(band.sum())
    if band_count == 0:
        return QACheck(rule=RULE_HALO, passed=True, measured=0.0, detail="경계 밴드 없음")

    rgb = final_rgba[..., :3].astype(np.float64)
    color_dist = np.sqrt(((rgb - np.asarray(bg_color, dtype=np.float64)) ** 2).sum(axis=-1))
    halo = band & (alpha / 255.0 >= thresholds.halo_alpha_min) & (
        color_dist < thresholds.halo_color_delta
    )
    ratio = float(halo.sum()) / band_count
    limit = (
        thresholds.halo_max_ratio_translucent
        if material == "translucent"
        else thresholds.halo_max_ratio
    )
    return QACheck(
        rule=RULE_HALO,
        passed=ratio <= limit,
        measured=round(ratio, 4),
        detail=f"경계 밴드 헤일로 비율 {ratio:.1%} (한도 {limit:.0%}, material={material})",
        regen_hint=(
            None if ratio <= limit else "경계에 배경색 헤일로가 남았습니다 — 디프린지·매팅 전략을 점검하세요"
        ),
    )


def run_qa(
    final_img: Image.Image,
    canvas_alpha: np.ndarray,
    material: MaterialClass,
    thresholds: QAThresholds | None = None,
    bg_color: tuple[int, int, int] = FORCED_BG_COLOR,
) -> list[QACheck]:
    """canvas_alpha = 크롭 전 원본 캔버스 알파(매팅 직후), final_img = 후처리 완료 이미지."""
    thresholds = thresholds or QAThresholds()
    final_rgba = np.asarray(final_img.convert("RGBA"))
    return [
        check_edge_contact(canvas_alpha, thresholds),
        check_coverage(canvas_alpha, thresholds),
        check_halo(final_rgba, material, thresholds, bg_color),
    ]
