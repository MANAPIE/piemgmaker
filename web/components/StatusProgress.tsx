// 진행 상태: 큐 대기 → 엔진 준비 중 → 생성 n/k → 후처리 → 완료/실패
import type { JobDetail } from "@/lib/api";
import { jobProgressPercent } from "@/lib/progress";

const STEPS: { key: string; label: string }[] = [
  { key: "queued", label: "큐 대기" },
  { key: "preparing", label: "엔진 준비" },
  { key: "running", label: "생성" },
  { key: "postprocess", label: "후처리·QA" },
  { key: "done", label: "완료" },
];

function detailLabel(detail: JobDetail): string | null {
  if (detail.state === "queued" && detail.queue_position != null && detail.queue_position > 0) {
    return `앞에 ${detail.queue_position}건`;
  }
  if (detail.state === "running") return `${detail.done}/${detail.total}`;
  return null;
}

export default function StatusProgress({ detail }: { detail: JobDetail }) {
  const failed = detail.state === "failed";
  const canceled = detail.state === "canceled";
  const currentIndex =
    failed || canceled ? STEPS.length : STEPS.findIndex((s) => s.key === detail.state);
  const extra = detailLabel(detail);

  return (
    <div className="rounded-lg border border-[var(--line)] bg-[var(--card)] px-5 py-4">
      <ol className="flex flex-wrap items-center gap-x-1 gap-y-2">
        {STEPS.map((step, index) => {
          const stateClass = failed || canceled
            ? "text-[var(--ink-soft)]/50"
            : index < currentIndex
              ? "text-[var(--ink-soft)]"
              : index === currentIndex
                ? "font-semibold text-[var(--mat)]"
                : "text-[var(--ink-soft)]/50";
          return (
            <li key={step.key} className="flex items-center gap-1 text-[13px]">
              <span className={stateClass}>
                {step.label}
                {index === currentIndex && extra && (
                  <span className="ml-1 font-mono text-xs">({extra})</span>
                )}
              </span>
              {index < STEPS.length - 1 && (
                <span aria-hidden className="mx-1.5 text-[var(--line)]">
                  ─
                </span>
              )}
            </li>
          );
        })}
        {failed && (
          <li className="ml-1 rounded bg-[var(--fail-tint)] px-2 py-0.5 text-[12px] font-semibold text-[var(--fail)]">
            실패
          </li>
        )}
        {canceled && (
          <li className="ml-1 rounded bg-[var(--warn-tint)] px-2 py-0.5 text-[12px] font-semibold text-[var(--warn)]">
            취소됨
          </li>
        )}
        {!failed && !canceled && detail.state !== "done" && (
          <li aria-hidden className="ml-1 h-2 w-2 animate-pulse rounded-full bg-[var(--mat)]" />
        )}
        {detail.strategy && detail.state === "done" && (
          <li className="ml-auto font-mono text-[11px] text-[var(--ink-soft)]">
            배경 제거: {detail.strategy}
          </li>
        )}
      </ol>
      {!failed && !canceled && detail.state !== "done" && (
        <div
          className="mt-3 h-1.5 overflow-hidden rounded-full bg-[var(--line)]"
          role="progressbar"
          aria-valuenow={jobProgressPercent(detail.state, detail.done, detail.total)}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div
            className="h-full rounded-full bg-[var(--mat)] transition-[width] duration-500"
            style={{ width: `${jobProgressPercent(detail.state, detail.done, detail.total)}%` }}
          />
        </div>
      )}
      {detail.error && (
        <p className="mt-3 whitespace-pre-wrap break-all rounded-md bg-[var(--fail-tint)] px-3 py-2 text-[13px] text-[var(--fail)]">
          {detail.error}
        </p>
      )}
    </div>
  );
}
