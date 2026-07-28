"use client";

// 자산 라이브러리 — 원본 그대로 삽입될 자산을 직접 등록·관리한다.
// 삭제는 없다: 숨김(soft delete)과 복원만 있다.

import { useCallback, useEffect, useState } from "react";
import {
  api,
  assetPreviewUrl,
  postForm,
  postJson,
  type AssetInfo,
} from "@/lib/api";

const field =
  "w-full rounded-md border border-[var(--line)] bg-white px-3 py-2 text-sm placeholder:text-[var(--ink-soft)]/60";
const label = "block text-[13px] font-semibold";

function RegisterForm({ onDone }: { onDone: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [assetId, setAssetId] = useState("");
  const [name, setName] = useState("");
  const [type, setType] = useState<"logo" | "object">("object");
  const [variantId, setVariantId] = useState("main");
  const [minScale, setMinScale] = useState(0.5);
  const [clearSpace, setClearSpace] = useState(16);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function pick(next: File | null) {
    setFile(next);
    setPreview((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return next ? URL.createObjectURL(next) : null;
    });
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!file) {
      setError("알파 PNG 파일을 선택하세요");
      return;
    }
    setBusy(true);
    setError(null);
    const form = new FormData();
    form.append("file", file);
    form.append("asset_id", assetId);
    form.append("name", name);
    form.append("asset_type", type);
    form.append("variant_id", variantId);
    form.append("min_scale", String(minScale));
    form.append("clear_space_px", String(clearSpace));
    try {
      await postForm("assets", form);
      pick(null);
      setAssetId("");
      setName("");
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      onSubmit={submit}
      className="rounded-lg border border-[var(--line)] bg-[var(--card)] p-5"
    >
      <h2 className="mb-1 font-mono text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--mat)]">
        새 자산 등록
      </h2>
      <p className="mb-4 text-xs text-[var(--ink-soft)]">
        알파 PNG만 등록됩니다. 완전 불투명 코어가 있어야 원본 보존 검증(코어 해시)이 가능합니다.
      </p>
      <div className="flex flex-wrap gap-5">
        <label className="checker-fine flex h-36 w-36 shrink-0 cursor-pointer items-center justify-center overflow-hidden rounded-md border border-dashed border-[var(--line)] hover:border-[var(--mat)]">
          {preview ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={preview} alt="미리보기" className="max-h-full max-w-full object-contain" />
          ) : (
            <span className="px-3 text-center text-xs text-[var(--ink-soft)]">
              PNG 선택
              <br />
              (알파 필수)
            </span>
          )}
          <input
            type="file"
            accept="image/png"
            className="hidden"
            onChange={(e) => pick(e.target.files?.[0] ?? null)}
          />
        </label>
        <div className="grid min-w-0 flex-1 grid-cols-2 gap-3">
          <div>
            <label className={label}>자산 id</label>
            <input
              value={assetId}
              onChange={(e) => setAssetId(e.target.value)}
              placeholder="link-logo"
              pattern="[a-z0-9][a-z0-9-]*"
              required
              className={`${field} mt-1 font-mono`}
            />
          </div>
          <div>
            <label className={label}>표시 이름</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="LINK 로고"
              required
              className={`${field} mt-1`}
            />
          </div>
          <div>
            <label className={label}>종류</label>
            <select
              value={type}
              onChange={(e) => setType(e.target.value as "logo" | "object")}
              className={`${field} mt-1`}
            >
              <option value="object">오브젝트</option>
              <option value="logo">로고</option>
            </select>
          </div>
          <div>
            <label className={label}>variant id</label>
            <input
              value={variantId}
              onChange={(e) => setVariantId(e.target.value)}
              pattern="[a-z0-9][a-z0-9-]*"
              required
              className={`${field} mt-1 font-mono`}
            />
          </div>
          <div>
            <label className={label}>최소 스케일</label>
            <input
              type="number"
              value={minScale}
              onChange={(e) => setMinScale(Number(e.target.value))}
              min={0.05}
              max={1}
              step={0.05}
              className={`${field} mt-1`}
            />
          </div>
          <div>
            <label className={label}>여백 (px)</label>
            <input
              type="number"
              value={clearSpace}
              onChange={(e) => setClearSpace(Number(e.target.value))}
              min={0}
              max={200}
              className={`${field} mt-1`}
            />
          </div>
        </div>
      </div>
      {error && (
        <p className="mt-3 rounded-md bg-[var(--fail-tint)] px-3 py-2 text-sm text-[var(--fail)]">
          {error}
        </p>
      )}
      <button
        disabled={busy}
        className="mt-4 rounded-md bg-[var(--mat)] px-5 py-2 text-sm font-semibold text-white hover:bg-[var(--mat-deep)] disabled:opacity-40"
      >
        {busy ? "등록 중…" : "등록"}
      </button>
    </form>
  );
}

function VariantAdder({ assetId, onDone }: { assetId: string; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  return (
    <label
      className={`cursor-pointer text-xs underline decoration-[var(--mat)] underline-offset-2 ${busy ? "opacity-40" : ""}`}
    >
      {busy ? "추가 중…" : "+ variant"}
      <input
        type="file"
        accept="image/png"
        className="hidden"
        disabled={busy}
        onChange={async (e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (!file) return;
          const variantId = prompt("variant id (kebab-case, 예: 3d-lettering)");
          if (!variantId) return;
          setBusy(true);
          try {
            const form = new FormData();
            form.append("file", file);
            form.append("variant_id", variantId);
            await postForm(`assets/${assetId}/variants`, form);
            onDone();
          } catch (err) {
            alert((err as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      />
    </label>
  );
}

export default function AssetsPage() {
  const [assets, setAssets] = useState<AssetInfo[]>([]);
  const [showArchived, setShowArchived] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    api<{ assets: AssetInfo[] }>(`assets?include_archived=${showArchived}`)
      .then((res) => {
        setAssets(res.assets);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, [showArchived]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function setArchived(assetId: string, variant: string | null, archived: boolean) {
    await postJson(`assets/${assetId}/archive`, { variant, archived });
    refresh();
  }

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold tracking-tight">자산 라이브러리</h1>
          <p className="mt-1 text-sm text-[var(--ink-soft)]">
            여기 등록된 자산만 생성 시 <b>원본 그대로</b> 합성됩니다. 로고 변형은 variant로
            추가하세요.
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={showArchived}
            onChange={(e) => setShowArchived(e.target.checked)}
            className="accent-[var(--mat)]"
          />
          숨김 항목 표시
        </label>
      </header>

      <RegisterForm onDone={refresh} />

      {error && (
        <p className="rounded-md bg-[var(--fail-tint)] px-4 py-3 text-sm text-[var(--fail)]">
          {error}
        </p>
      )}

      {assets.length === 0 ? (
        <p className="rounded-lg border border-[var(--line)] bg-[var(--card)] px-4 py-10 text-center text-sm text-[var(--ink-soft)]">
          등록된 자산이 없습니다 — 위에서 첫 자산을 올려보세요
        </p>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {assets.map((asset) => (
            <article
              key={asset.id}
              className={`rounded-lg border border-[var(--line)] bg-[var(--card)] p-4 ${
                asset.archived ? "opacity-60" : ""
              }`}
            >
              <div className="mb-3 flex items-start justify-between gap-2">
                <div>
                  <h3 className="text-sm font-semibold">
                    {asset.name}
                    <span className="ml-2 rounded bg-[var(--mat-tint)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--mat-deep)]">
                      {asset.type}
                    </span>
                    {asset.archived && (
                      <span className="ml-1.5 rounded bg-[var(--warn-tint)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--warn)]">
                        숨김
                      </span>
                    )}
                  </h3>
                  <p className="mt-0.5 font-mono text-[11px] text-[var(--ink-soft)]">
                    {asset.id} · min×{asset.usage.min_scale} · 여백 {asset.usage.clear_space_px}px
                  </p>
                </div>
                <div className="flex items-center gap-3">
                  <VariantAdder assetId={asset.id} onDone={refresh} />
                  <button
                    onClick={() => setArchived(asset.id, null, !asset.archived)}
                    className="text-xs underline underline-offset-2"
                  >
                    {asset.archived ? "복원" : "숨김"}
                  </button>
                </div>
              </div>
              <div className="flex flex-wrap gap-3">
                {asset.variants.map((variant) => (
                  <div key={variant.id} className={variant.archived ? "opacity-50" : ""}>
                    <div className="checker-fine flex h-24 w-24 items-center justify-center overflow-hidden rounded-md border border-[var(--line)]">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={assetPreviewUrl(asset.id, variant.id)}
                        alt={`${asset.name} ${variant.id}`}
                        className="max-h-full max-w-full object-contain"
                      />
                    </div>
                    <div className="mt-1 flex items-center justify-between">
                      <span className="font-mono text-[10px] text-[var(--ink-soft)]">
                        {variant.id}
                      </span>
                      <button
                        onClick={() => setArchived(asset.id, variant.id, !variant.archived)}
                        className="text-[10px] underline underline-offset-2"
                      >
                        {variant.archived ? "복원" : "숨김"}
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
