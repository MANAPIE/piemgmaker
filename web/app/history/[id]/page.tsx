"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useCallback, useEffect, useState } from "react";
import BackgroundToggle, { type PreviewBg } from "@/components/BackgroundToggle";
import CandidateCard from "@/components/CandidateCard";
import JobSettings from "@/components/JobSettings";
import StatusProgress from "@/components/StatusProgress";
import { api, postJson, type JobDetail } from "@/lib/api";

const PENDING = new Set(["queued", "preparing", "running", "postprocess"]);

export default function JobPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const [detail, setDetail] = useState<JobDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [bg, setBg] = useState<PreviewBg>("checker");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [confirmed, setConfirmed] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const next = await api<JobDetail>(`jobs/${id}`);
      setDetail(next);
      setError(null);
      if (next.selection) {
        setSelected(new Set(next.selection));
        setConfirmed(true);
      }
    } catch (e) {
      setError((e as Error).message);
    }
  }, [id]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (!detail || !PENDING.has(detail.state)) return;
    const timer = setInterval(refresh, 2000);
    return () => clearInterval(timer);
  }, [detail, refresh]);

  async function rerunNewSeed() {
    const { job_id } = await postJson<{ job_id: string }>(`jobs/${id}/rerun`, { new_seed: true });
    router.push(`/history/${job_id}`);
  }

  async function confirmSelection() {
    await postJson(`jobs/${id}/select`, { indices: [...selected] });
    setConfirmed(true);
  }

  if (error) {
    return (
      <p className="rounded-md bg-[var(--fail-tint)] px-4 py-3 text-sm text-[var(--fail)]">
        {error}
      </p>
    );
  }
  if (!detail) {
    return <p className="text-sm text-[var(--ink-soft)]">불러오는 중…</p>;
  }

  const zipQuery = selected.size > 0 ? `?indices=${[...selected].join(",")}` : "";

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link
            href="/history"
            className="font-mono text-[11px] text-[var(--ink-soft)] underline-offset-2 hover:underline"
          >
            ← 히스토리
          </Link>
          <h1 className="mt-1 text-xl font-bold tracking-tight">리뷰 · 확정</h1>
          <p className="mt-1 font-mono text-[11px] text-[var(--ink-soft)]">
            {detail.job_id}
            {detail.pins.model_id && ` · ${detail.pins.model_id}`}
            {detail.pins.style_packs?.length
              ? ` · ${detail.pins.style_packs.map((p) => `${p.id}@${p.version}`).join(" ")}`
              : ""}
          </p>
          {detail.rerun_of && (
            <p className="mt-0.5 text-xs text-[var(--ink-soft)]">
              <Link href={`/history/${detail.rerun_of}`} className="underline underline-offset-2">
                원본 잡
              </Link>
              의 재생성
            </p>
          )}
        </div>
        <div className="flex gap-2">
          <button
            onClick={rerunNewSeed}
            className="rounded-md border border-[var(--line)] bg-white px-3.5 py-2 text-sm hover:border-[var(--mat)]/50"
          >
            같은 설정 + 새 시드
          </button>
          <Link
            href={`/?from=${detail.job_id}`}
            className="rounded-md border border-[var(--line)] bg-white px-3.5 py-2 text-sm hover:border-[var(--mat)]/50"
          >
            입력 수정 후 재생성
          </Link>
        </div>
      </header>

      <StatusProgress detail={detail} />

      <JobSettings detail={detail} />

      {detail.candidates.length > 0 && (
        <>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <BackgroundToggle value={bg} onChange={setBg} />
            <div className="flex items-center gap-2">
              <button
                onClick={confirmSelection}
                disabled={selected.size === 0}
                className="rounded-md bg-[var(--mat)] px-4 py-2 text-sm font-semibold text-white hover:bg-[var(--mat-deep)] disabled:opacity-40"
              >
                {confirmed ? "확정 갱신" : "확정"} ({selected.size})
              </button>
              <a
                href={`/api/pm/jobs/${detail.job_id}/export${zipQuery}`}
                aria-disabled={selected.size === 0}
                className={`rounded-md border border-[var(--line)] bg-white px-4 py-2 text-sm ${
                  selected.size === 0 ? "pointer-events-none opacity-40" : "hover:border-[var(--mat)]/50"
                }`}
              >
                ZIP 다운로드
              </a>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-4">
            {detail.candidates.map((candidate) => (
              <CandidateCard
                key={candidate.index}
                jobId={detail.job_id}
                candidate={candidate}
                bg={bg}
                selected={selected.has(candidate.index)}
                onToggle={() =>
                  setSelected((prev) => {
                    const next = new Set(prev);
                    if (next.has(candidate.index)) next.delete(candidate.index);
                    else next.add(candidate.index);
                    return next;
                  })
                }
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
