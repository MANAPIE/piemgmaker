"use client";

// 전 페이지 공통 미니 진행 표시 — 사이드바 하단, 5초 폴링. 아무 것도 안 돌면 숨긴다.

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import {
  QUEUE_STATE_LABEL,
  jobProgressPercent,
  type QueueSnapshot,
} from "@/lib/progress";

export default function SidebarStatus() {
  const [snapshot, setSnapshot] = useState<QueueSnapshot | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api<QueueSnapshot>("queue")
        .then((data) => alive && setSnapshot(data))
        .catch(() => alive && setSnapshot(null));
    load();
    const timer = setInterval(load, 5000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  if (!snapshot) return null;
  const { running, queued, engine } = snapshot;
  const active = running || queued.length > 0 || (engine?.external ?? 0) > 0;
  if (!active) return null;

  return (
    <div className="mx-3 mb-3 rounded-md bg-white/10 px-3 py-2.5">
      {running ? (
        <Link href={`/history/${running.job_id}`} className="block">
          <p className="truncate text-[11px] font-medium text-white/90">
            {running.object_concept}
          </p>
          <p className="mt-0.5 font-mono text-[10px] text-white/60">
            {QUEUE_STATE_LABEL[running.state]}
            {running.state === "running" && ` ${running.done}/${running.total}`}
            {queued.length > 0 && ` · 대기 ${queued.length}`}
          </p>
          <div
            className="mt-1.5 h-1 overflow-hidden rounded-full bg-white/20"
            role="progressbar"
            aria-valuenow={jobProgressPercent(running.state, running.done, running.total)}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <div
              className="h-full rounded-full bg-[var(--paper)] transition-[width] duration-500"
              style={{
                width: `${jobProgressPercent(running.state, running.done, running.total)}%`,
              }}
            />
          </div>
        </Link>
      ) : queued.length > 0 ? (
        <p className="font-mono text-[10px] text-white/70">대기열 {queued.length}건</p>
      ) : (
        <div>
          <p className="flex items-center gap-1.5 text-[11px] text-white/80">
            <span aria-hidden className="h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--warn)]" />
            엔진 외부 작업 중
          </p>
          <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-white/20">
            <div className="h-full w-1/3 animate-pulse rounded-full bg-white/50" />
          </div>
        </div>
      )}
    </div>
  );
}
