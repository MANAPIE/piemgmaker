"use client";

// 하단 샘플 갤러리 — 모델×스타일 팩 실제 산출물. samples API로 읽는다.

import { useEffect, useMemo, useState } from "react";
import { api, fetchSamples, sampleUrl, type SampleInfo, type StylePackOption } from "@/lib/api";

const PATH_LABEL: Record<string, string> = {
  segment: "세그먼트 매팅",
  trimap: "트라이맵 매팅",
  native_alpha: "네이티브 알파",
  styleref: "참조 이미지",
  logoref: "로고 참조 생성",
  imprint: "표면 새김 합성",
};

const OPEN_KEY = "pm-sample-gallery-open";

export default function SampleGallery() {
  const [samples, setSamples] = useState<SampleInfo[]>([]);
  const [packNames, setPackNames] = useState<Record<string, string>>({});
  const [model, setModel] = useState("");
  const [pack, setPack] = useState("");
  const [open, setOpen] = useState(false);

  useEffect(() => {
    fetchSamples().then(setSamples);
    api<{ packs: StylePackOption[] }>("style-packs")
      .then((res) =>
        setPackNames(Object.fromEntries(res.packs.map((p) => [p.id, p.name]))),
      )
      .catch(() => setPackNames({}));
    setOpen(localStorage.getItem(OPEN_KEY) === "1");
  }, []);

  function toggleOpen() {
    setOpen((prev) => {
      localStorage.setItem(OPEN_KEY, prev ? "0" : "1");
      return !prev;
    });
  }

  // 팩 없이 만든 샘플(포토리얼 free_text 등)은 pack이 빈 문자열이다
  const packName = (id: string) => (id ? packNames[id] ?? id : "팩 없음");
  const models = useMemo(() => [...new Set(samples.map((s) => s.model_label))], [samples]);
  const packs = useMemo(() => [...new Set(samples.map((s) => s.pack))], [samples]);
  const visible = samples.filter(
    (s) => (!model || s.model_label === model) && (!pack || s.pack === pack),
  );

  if (samples.length === 0) return null;

  return (
    <section className="overflow-hidden rounded-lg border border-[var(--line)] bg-[var(--card)]">
      <button
        type="button"
        onClick={toggleOpen}
        aria-expanded={open}
        className="flex w-full items-center justify-between px-5 py-4 text-left hover:bg-[var(--paper)]/50"
      >
        <div>
          <h2 className="font-mono text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--mat)]">
            샘플 갤러리 ({samples.length})
          </h2>
          <p className="mt-1 text-xs text-[var(--ink-soft)]">
            모델·스타일 팩별 실제 산출물입니다 — 방향을 잡을 때 참고하세요
          </p>
        </div>
        <span
          aria-hidden
          className={`text-sm text-[var(--ink-soft)] transition-transform ${open ? "rotate-180" : ""}`}
        >
          ▾
        </span>
      </button>
      {open && (
        <div className="border-t border-[var(--line)] p-5">
      <div className="mb-4 flex flex-wrap items-center justify-end gap-3">
        <div className="flex flex-wrap gap-1.5">
          <select
            value={model}
            onChange={(e) => setModel(e.target.value)}
            className="rounded-md border border-[var(--line)] bg-white px-2 py-1.5 text-xs"
          >
            <option value="">모든 모델</option>
            {models.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
          <select
            value={pack}
            onChange={(e) => setPack(e.target.value)}
            className="rounded-md border border-[var(--line)] bg-white px-2 py-1.5 text-xs"
          >
            <option value="">모든 스타일 팩</option>
            {packs.map((p) => (
              <option key={p} value={p}>
                {packName(p)}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        {visible.map((sample) => (
          <figure
            key={sample.id}
            className="overflow-hidden rounded-md border border-[var(--line)]"
          >
            <div className="checker flex aspect-square items-center justify-center p-2">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={sampleUrl(sample.file)}
                alt={sample.subject}
                loading="lazy"
                className="max-h-full max-w-full object-contain"
              />
            </div>
            <figcaption className="border-t border-[var(--line)] px-2.5 py-2">
              <p className="truncate text-xs font-medium" title={sample.subject}>
                {sample.subject}
              </p>
              <p className="mt-0.5 text-[10px] text-[var(--ink-soft)]">
                {sample.model_label} · {packName(sample.pack)}
              </p>
              <p className="font-mono text-[10px] text-[var(--ink-soft)]/70">
                {PATH_LABEL[sample.path] ?? sample.path} · seed {sample.seed}
              </p>
            </figcaption>
          </figure>
        ))}
      </div>
        </div>
      )}
    </section>
  );
}
