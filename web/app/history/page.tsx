"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  api,
  imageUrl,
  type HistoryPage,
  type StylePackOption,
} from "@/lib/api";

const STATE_BADGE: Record<string, string> = {
  done: "bg-[var(--mat-tint)] text-[var(--mat-deep)]",
  failed: "bg-[var(--fail-tint)] text-[var(--fail)]",
};

export default function HistoryPage() {
  const [data, setData] = useState<HistoryPage | null>(null);
  const [packs, setPacks] = useState<StylePackOption[]>([]);
  const [query, setQuery] = useState("");
  const [pack, setPack] = useState("");
  const [page, setPage] = useState(1);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<{ packs: StylePackOption[] }>("style-packs")
      .then((res) => setPacks(res.packs))
      .catch(() => setPacks([]));
  }, []);

  useEffect(() => {
    const params = new URLSearchParams({ page: String(page) });
    if (query) params.set("q", query);
    if (pack) params.set("pack", pack);
    api<HistoryPage>(`jobs?${params}`)
      .then((res) => {
        setData(res);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, [query, pack, page]);

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold tracking-tight">히스토리</h1>
          <p className="mt-1 text-sm text-[var(--ink-soft)]">지금까지의 생성 기록입니다</p>
        </div>
        <div className="flex gap-2">
          <input
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setPage(1);
            }}
            placeholder="캠페인·요소 검색"
            className="w-56 rounded-md border border-[var(--line)] bg-white px-3 py-2 text-sm"
          />
          <select
            value={pack}
            onChange={(e) => {
              setPack(e.target.value);
              setPage(1);
            }}
            className="rounded-md border border-[var(--line)] bg-white px-3 py-2 text-sm"
          >
            <option value="">모든 스타일 팩</option>
            {packs.map((option) => (
              <option key={option.id} value={option.id}>
                {option.name}
              </option>
            ))}
          </select>
        </div>
      </header>

      {error && (
        <p className="rounded-md bg-[var(--fail-tint)] px-4 py-3 text-sm text-[var(--fail)]">
          {error}
        </p>
      )}

      <div className="overflow-hidden rounded-lg border border-[var(--line)] bg-[var(--card)]">
        <ul className="divide-y divide-[var(--line)]">
          {data?.items.map((item) => (
            <li key={item.job_id} className="flex items-center gap-4 px-4 py-3 hover:bg-[var(--paper)]/60">
              <Link href={`/history/${item.job_id}`} className="shrink-0">
                <span className="checker-fine flex h-14 w-14 items-center justify-center overflow-hidden rounded-md border border-[var(--line)]">
                  {item.thumb ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={imageUrl(item.job_id, item.thumb)}
                      alt=""
                      className="max-h-full max-w-full object-contain"
                    />
                  ) : (
                    <span className="text-xs text-[var(--ink-soft)]/40">—</span>
                  )}
                </span>
              </Link>
              <Link href={`/history/${item.job_id}`} className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{item.campaign_text}</p>
                <p className="truncate text-xs text-[var(--ink-soft)]">{item.object_concept}</p>
                <p className="mt-0.5 font-mono text-[10px] text-[var(--ink-soft)]/80">
                  {item.packs.join(" ") || "—"}
                  {item.created_at &&
                    ` · ${new Date(item.created_at * 1000).toLocaleString("ko-KR")}`}
                </p>
              </Link>
              <span
                className={`shrink-0 rounded px-2 py-0.5 font-mono text-[10px] font-semibold uppercase ${
                  STATE_BADGE[item.state] ?? "bg-[var(--mat-tint)]/50 text-[var(--ink-soft)]"
                }`}
              >
                {item.state}
                {item.selected ? " · 확정" : ""}
              </span>
            </li>
          ))}
          {data && data.items.length === 0 && (
            <li className="px-4 py-10 text-center text-sm text-[var(--ink-soft)]">
              기록이 없습니다 —{" "}
              <Link href="/" className="underline underline-offset-2">
                첫 오브젝트를 생성
              </Link>
              해 보세요
            </li>
          )}
        </ul>
      </div>

      {data && data.total > data.page_size && (
        <div className="flex items-center gap-3 text-sm">
          <button
            disabled={page <= 1}
            onClick={() => setPage(page - 1)}
            className="rounded-md border border-[var(--line)] bg-white px-3 py-1.5 disabled:opacity-40"
          >
            이전
          </button>
          <span className="font-mono text-xs">
            {page} / {totalPages}
          </span>
          <button
            disabled={page >= totalPages}
            onClick={() => setPage(page + 1)}
            className="rounded-md border border-[var(--line)] bg-white px-3 py-1.5 disabled:opacity-40"
          >
            다음
          </button>
        </div>
      )}
    </div>
  );
}
