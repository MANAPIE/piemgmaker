"""알파 QA 리포트 스키마 — 후보별 하드 룰 판정 + 재생성 힌트."""

from pydantic import BaseModel, Field, computed_field

from piemgmaker.schemas.style_pack import MaterialClass


class QACheck(BaseModel):
    rule: str
    passed: bool
    measured: float | None = None
    detail: str = ""
    regen_hint: str | None = None


class CandidateQA(BaseModel):
    index: int
    seed: int
    checks: list[QACheck] = Field(default_factory=list)

    @computed_field
    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)


class QAReport(BaseModel):
    job_id: str
    material_class: MaterialClass
    candidates: list[CandidateQA] = Field(default_factory=list)

    @computed_field
    @property
    def passed(self) -> bool:
        return bool(self.candidates) and all(c.passed for c in self.candidates)
