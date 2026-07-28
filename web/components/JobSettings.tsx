"use client";

// 잡 상세 설정 패널 — 히스토리 하위 리뷰 페이지에서 브리프 전문·버전 핀을 펼쳐 본다.

import { useEffect, useState } from "react";
import {
  api,
  uploadPreviewUrl,
  type JobDetail,
  type StylePackOption,
} from "@/lib/api";

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5 py-2 sm:flex-row sm:items-baseline sm:gap-4">
      <dt className="w-28 shrink-0 font-mono text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--ink-soft)]/80">
        {label}
      </dt>
      <dd className="min-w-0 flex-1 text-sm">{children}</dd>
    </div>
  );
}

function sizeLabel(size: string | { width: number; height: number }): string {
  if (typeof size === "string") return size;
  return `${size.width}×${size.height}`;
}

export default function JobSettings({ detail }: { detail: JobDetail }) {
  const [open, setOpen] = useState(true);
  const [packNames, setPackNames] = useState<Record<string, string>>({});

  useEffect(() => {
    api<{ packs: StylePackOption[] }>("style-packs")
      .then((res) =>
        setPackNames(Object.fromEntries(res.packs.map((p) => [p.id, p.name])))
      )
      .catch(() => setPackNames({}));
  }, []);

  const brief = detail.brief;
  const pins = detail.pins;
  const packPins = pins.style_packs ?? [];
  const refs = brief?.reference_images ?? [];

  return (
    <section className="rounded-lg border border-[var(--line)] bg-[var(--card)]">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between px-4 py-3 text-left"
      >
        <span className="font-mono text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--mat)]">
          상세 설정
        </span>
        <span
          aria-hidden
          className={`text-xs text-[var(--ink-soft)] transition-transform ${open ? "rotate-180" : ""}`}
        >
          ▾
        </span>
      </button>

      {open && (
        <div className="border-t border-[var(--line)] px-4 pb-4 pt-2">
          {!brief ? (
            <p className="py-2 text-sm text-[var(--ink-soft)]">
              이 잡에는 저장된 브리프가 없습니다
            </p>
          ) : (
            <dl className="divide-y divide-[var(--line)]/70">
              <Row label="캠페인">{brief.campaign_text || "—"}</Row>
              <Row label="오브젝트">{brief.object_concept || "—"}</Row>
              <Row label="모델">
                <span className="font-mono text-xs">
                  {pins.model_id ?? brief.model ?? "기본"}
                </span>
              </Row>
              <Row label="스타일 팩">
                {packPins.length > 0 ? (
                  <span className="flex flex-wrap gap-1.5">
                    {packPins.map((p) => (
                      <span
                        key={p.id}
                        className="rounded bg-[var(--mat-tint)]/60 px-2 py-0.5 text-xs"
                      >
                        {packNames[p.id] ?? p.id}
                        <span className="ml-1 font-mono text-[10px] text-[var(--ink-soft)]">
                          {p.id}@{p.version}
                        </span>
                      </span>
                    ))}
                  </span>
                ) : (
                  <span className="text-[var(--ink-soft)]">없음</span>
                )}
              </Row>
              {brief.style?.free_text && (
                <Row label="자유 스타일">{brief.style.free_text}</Row>
              )}
              {refs.length > 0 && (
                <Row label="참조 이미지">
                  <span className="flex flex-wrap gap-2">
                    {refs.map((ref) => (
                      <span
                        key={ref}
                        className="checker-fine flex h-16 w-16 items-center justify-center overflow-hidden rounded-md border border-[var(--line)]"
                      >
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                          src={uploadPreviewUrl(ref)}
                          alt=""
                          className="max-h-full max-w-full object-contain"
                        />
                      </span>
                    ))}
                  </span>
                </Row>
              )}
              {brief.assets.length > 0 && (
                <Row label="삽입 자산">
                  <span className="font-mono text-xs">{brief.assets.join(", ")}</span>
                </Row>
              )}
              <Row label="크기">
                <span className="font-mono text-xs">{sizeLabel(brief.size_preset)}</span>
              </Row>
              <Row label="후보 수">
                <span className="font-mono text-xs">{brief.candidate_count}장</span>
              </Row>
              <Row label="시드">
                <span className="font-mono text-xs">
                  {brief.seed === "random" ? "랜덤" : brief.seed}
                  {detail.candidates.length > 0 &&
                    ` → ${detail.candidates.map((c) => c.seed).join(", ")}`}
                </span>
              </Row>
              {brief.negative && <Row label="네거티브">{brief.negative}</Row>}
              {brief.placement_hint && (
                <Row label="배치 힌트">
                  {brief.placement_hint.asset_position}
                  {brief.placement_hint.composition &&
                    ` · ${brief.placement_hint.composition}`}
                </Row>
              )}
              <Row label="매팅">
                <span className="font-mono text-xs">
                  {pins.matting_chain?.join(" → ") ?? "—"}
                  {detail.strategy && ` (사용: ${detail.strategy})`}
                </span>
              </Row>
              <Row label="워크플로우">
                <span className="font-mono text-xs">
                  {pins.workflow
                    ? `${pins.workflow.id} · ${pins.workflow.hash.slice(0, 12)}`
                    : "—"}
                </span>
              </Row>
            </dl>
          )}
        </div>
      )}
    </section>
  );
}
