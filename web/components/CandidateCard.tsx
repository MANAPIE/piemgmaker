"use client";

import { imageUrl, type Candidate } from "@/lib/api";
import { previewStyle, type PreviewBg } from "@/components/BackgroundToggle";

export default function CandidateCard({
  jobId,
  candidate,
  bg,
  selected,
  onToggle,
}: {
  jobId: string;
  candidate: Candidate;
  bg: PreviewBg;
  selected: boolean;
  onToggle: () => void;
}) {
  const rel = candidate.final ?? candidate.candidate;
  const failedChecks = candidate.checks.filter((check) => !check.passed);
  return (
    <figure
      className={`overflow-hidden rounded-lg border bg-[var(--card)] transition-shadow ${
        selected
          ? "border-[var(--mat)] shadow-[0_0_0_2px_var(--mat-tint)]"
          : "border-[var(--line)] hover:shadow-sm"
      }`}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-pressed={selected}
        className="block w-full cursor-pointer"
        title={selected ? "선택 해제" : "선택"}
      >
        <div className="flex aspect-square items-center justify-center p-3" style={previewStyle(bg)}>
          {rel ? (
            // 알파 PNG 원본 검수가 목적 — next/image 최적화 없이 그대로 표시
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={imageUrl(jobId, rel)}
              alt={`후보 ${candidate.index}`}
              className="max-h-full max-w-full object-contain drop-shadow-sm"
            />
          ) : (
            <span className="text-sm text-[var(--ink-soft)]">산출물 없음</span>
          )}
        </div>
      </button>
      <figcaption className="space-y-2 border-t border-[var(--line)] p-3">
        <div className="flex items-center justify-between">
          <span
            className={`rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide ${
              candidate.passed
                ? "bg-[var(--mat-tint)] text-[var(--mat-deep)]"
                : "bg-[var(--fail-tint)] text-[var(--fail)]"
            }`}
          >
            QA {candidate.passed ? "pass" : "fail"}
          </span>
          <span className="font-mono text-[11px] text-[var(--ink-soft)]">
            seed {candidate.seed}
          </span>
        </div>
        <div className="flex items-center justify-between text-xs">
          <label className="flex cursor-pointer items-center gap-1.5">
            <input type="checkbox" checked={selected} onChange={onToggle} className="accent-[var(--mat)]" />
            선택
          </label>
          {rel && (
            <a
              href={imageUrl(jobId, rel)}
              download={`PIEmgmaker_${jobId}_${candidate.index}.png`}
              className="underline decoration-[var(--mat)] underline-offset-2"
            >
              PNG 저장
            </a>
          )}
        </div>
        {failedChecks.length > 0 && (
          <details className="text-xs text-[var(--ink-soft)]">
            <summary className="cursor-pointer">실패 사유 {failedChecks.length}건</summary>
            <ul className="mt-1.5 space-y-1.5">
              {failedChecks.map((check) => (
                <li key={check.rule}>
                  <span className="font-mono text-[10px] uppercase">{check.rule}</span> —{" "}
                  {check.detail}
                  {check.regen_hint && (
                    <div className="mt-0.5 text-[var(--warn)]">→ {check.regen_hint}</div>
                  )}
                </li>
              ))}
            </ul>
          </details>
        )}
      </figcaption>
    </figure>
  );
}
