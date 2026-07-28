"use client";

// 실시간 대기열·진행 패널 — 3초 폴링. 대기 항목은 입력 프롬프트 요약을 함께 보여주고,
// 대기·실행 중 잡 모두 취소할 수 있다. 웹 잡이 없어도 엔진(외부 배치)이 바쁘면 표시한다.

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, postJson } from "@/lib/api";
import {
  QUEUE_STATE_LABEL,
  jobProgressPercent,
  type QueueJob,
  type QueueSnapshot,
} from "@/lib/progress";

function promptSummary(job: QueueJob): string {
  const parts = [job.campaign_text];
  if (job.free_text) parts.push(`스타일: ${job.free_text}`);
  return parts.filter(Boolean).join(" · ");
}

function metaLine(job: QueueJob): string {
  const parts: string[] = [];
  if (job.style_packs?.length) parts.push(job.style_packs.join(" "));
  if (job.model) parts.push(job.model);
  if (job.candidate_count) parts.push(`${job.candidate_count}장`);
  if (job.seed !== undefined && job.seed !== null) parts.push(`seed ${job.seed}`);
  return parts.join(" · ");
}

export default function QueuePanel() {
  const [snapshot, setSnapshot] = useState<QueueSnapshot | null>(null);
  const [canceling, setCanceling] = useState<Set<string>>(new Set());

  useEffect(() => {
    let alive = true;
    const load = () =>
      api<QueueSnapshot>("queue")
        .then((data) => alive && setSnapshot(data))
        .catch(() => alive && setSnapshot(null));
    load();
    const timer = setInterval(load, 3000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  async function cancelJob(job: QueueJob) {
    const label = job.state === "queued" ? "대기 중인 잡을" : "실행 중인 잡을";
    if (!confirm(`${label} 취소할까요?\n"${job.object_concept}"`)) return;
    setCanceling((prev) => new Set(prev).add(job.job_id));
    try {
      await postJson(`jobs/${job.job_id}/cancel`, {});
      const next = await api<QueueSnapshot>("queue");
      setSnapshot(next);
    } catch (e) {
      alert((e as Error).message);
    } finally {
      setCanceling((prev) => {
        const next = new Set(prev);
        next.delete(job.job_id);
        return next;
      });
    }
  }

  if (!snapshot) return null;
  const { running, queued, engine } = snapshot;
  const externalCount = engine?.external ?? 0;
  const engineOnlyBusy = !running && queued.length === 0 && externalCount > 0;
  if (!running && queued.length === 0 && !engineOnlyBusy) return null;

  const cancelBtn = (job: QueueJob, subtle = false) => (
    <button
      type="button"
      onClick={(e) => {
        e.preventDefault();
        cancelJob(job);
      }}
      disabled={canceling.has(job.job_id)}
      className={`shrink-0 rounded-md border px-2.5 py-1 text-[11px] transition-colors disabled:opacity-40 ${
        subtle
          ? "border-[var(--line)] bg-white text-[var(--ink-soft)] hover:border-[var(--fail)]/50 hover:text-[var(--fail)]"
          : "border-[var(--fail)]/40 bg-white text-[var(--fail)] hover:bg-[var(--fail-tint)]"
      }`}
    >
      {canceling.has(job.job_id) ? "취소 중…" : job.state === "queued" ? "취소" : "생성 중단"}
    </button>
  );

  return (
    <section className="rounded-lg border border-[var(--line)] bg-[var(--card)] p-4">
      <h2 className="mb-3 font-mono text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--mat)]">
        실시간 대기열
      </h2>
      {running && (
        <div className="rounded-md border border-[var(--mat)]/30 bg-[var(--mat-tint)]/40 px-3 py-2.5">
          <div className="flex items-start justify-between gap-3">
            <Link href={`/history/${running.job_id}`} className="min-w-0 flex-1 hover:underline">
              <p className="truncate text-sm font-medium">{running.object_concept}</p>
              <p className="truncate text-xs text-[var(--ink-soft)]">{promptSummary(running)}</p>
              <p className="mt-0.5 truncate font-mono text-[10px] text-[var(--ink-soft)]/80">
                {metaLine(running)}
              </p>
            </Link>
            <div className="flex shrink-0 items-center gap-2">
              <span className="font-mono text-[11px] font-semibold text-[var(--mat)]">
                {QUEUE_STATE_LABEL[running.state]}
                {running.state === "running" && ` ${running.done}/${running.total}`}
              </span>
              {cancelBtn(running)}
            </div>
          </div>
          <div
            className="mt-2 h-1.5 overflow-hidden rounded-full bg-[var(--line)]"
            role="progressbar"
            aria-valuenow={jobProgressPercent(running.state, running.done, running.total)}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <div
              className="h-full rounded-full bg-[var(--mat)] transition-[width] duration-500"
              style={{
                width: `${jobProgressPercent(running.state, running.done, running.total)}%`,
              }}
            />
          </div>
        </div>
      )}
      {queued.length > 0 && (
        <ul className="mt-2 divide-y divide-[var(--line)] rounded-md border border-[var(--line)]">
          {queued.map((job) => (
            <li key={job.job_id} className="flex items-center gap-3 px-3 py-2">
              <span className="shrink-0 font-mono text-[11px] text-[var(--ink-soft)]">
                #{(job.queue_position ?? 0) + 1}
              </span>
              <Link href={`/history/${job.job_id}`} className="min-w-0 flex-1 hover:underline">
                <p className="truncate text-sm">{job.object_concept}</p>
                <p className="truncate text-xs text-[var(--ink-soft)]">{promptSummary(job)}</p>
                <p className="truncate font-mono text-[10px] text-[var(--ink-soft)]/80">
                  {metaLine(job)}
                </p>
              </Link>
              {cancelBtn(job, true)}
            </li>
          ))}
        </ul>
      )}
      {engineOnlyBusy && (
        <div className="rounded-md border border-dashed border-[var(--line)] px-3 py-2.5">
          <div className="flex items-center gap-2 text-sm text-[var(--ink-soft)]">
            <span aria-hidden className="h-2 w-2 animate-pulse rounded-full bg-[var(--warn)]" />
            생성 엔진이 외부 작업(배치) 중입니다 — 새 잡은 그 뒤에 실행됩니다
          </div>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-[var(--line)]">
            <div className="h-full w-1/3 animate-pulse rounded-full bg-[var(--warn)]/60" />
          </div>
        </div>
      )}
      {running && externalCount > 0 && (
        <p className="mt-2 text-[11px] text-[var(--ink-soft)]">
          엔진 큐에 외부 작업 {externalCount}건이 함께 대기 중입니다
        </p>
      )}
    </section>
  );
}
