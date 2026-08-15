"use client";

// object_concept("AI가 그릴 요소")와 assets("라이브러리 자산 합성")의
// 라벨·설명 분리를 강제한다. 자산 픽셀 보존 수준은 합성 모드(팩 기본/overlay/imprint)가
// 결정하며, 로고 참조 생성(logoref)은 생성 트랙이라 보존이 없다. 모델 선택·참조
// 이미지·자산·로고 참조는 서로의 제약을 폼에서 안내하고, 최종 검증은 서버 스키마가 담당한다.

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import {
  api,
  assetPreviewUrl,
  fetchSamples,
  postForm,
  postJson,
  sampleUrl,
  uploadPreviewUrl,
  type AssetInfo,
  type BriefInput,
  type JobDetail,
  type ModelOption,
  type ModelsResponse,
  type SampleInfo,
  type StylePackOption,
} from "@/lib/api";

const CUSTOM_SIZE = "custom";

const field =
  "w-full rounded-md border border-[var(--line)] bg-white px-3 py-2 text-sm placeholder:text-[var(--ink-soft)]/60";
const label = "block text-[13px] font-semibold";
const help = "mt-0.5 text-xs text-[var(--ink-soft)]";

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border border-[var(--line)] bg-[var(--card)] p-5">
      <h2 className="mb-4 font-mono text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--mat)]">
        {title}
      </h2>
      <div className="space-y-4">{children}</div>
    </section>
  );
}

function Chip({
  active,
  onClick,
  children,
  title,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
  title?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className={`rounded-md border px-3 py-1.5 text-sm transition-colors ${
        active
          ? "border-[var(--mat)] bg-[var(--mat)] text-white"
          : "border-[var(--line)] bg-white hover:border-[var(--mat)]/50"
      }`}
    >
      {children}
    </button>
  );
}

export default function BriefForm({ fromJobId }: { fromJobId?: string }) {
  const router = useRouter();
  const [packs, setPacks] = useState<StylePackOption[]>([]);
  const [presets, setPresets] = useState<Record<string, number[]>>({});
  const [assets, setAssets] = useState<AssetInfo[]>([]);
  const [models, setModels] = useState<ModelOption[]>([]);
  // null = 아직 모름. 모르는 상태를 "로컬"로 단정하면 원격인데 로컬로 표시되므로 배지를 감춘다.
  const [engineFlavor, setEngineFlavor] = useState<"local" | "remote" | null>(null);
  const [samples, setSamples] = useState<SampleInfo[]>([]);

  const [campaignText, setCampaignText] = useState("");
  const [objectConcept, setObjectConcept] = useState("");
  const [model, setModel] = useState<string>("");
  const [selectedAssets, setSelectedAssets] = useState<string[]>([]);
  const [selectedPacks, setSelectedPacks] = useState<string[]>([]);
  const [freeText, setFreeText] = useState("");
  const [refs, setRefs] = useState<string[]>([]);
  const [refBusy, setRefBusy] = useState(false);
  const [sizeKey, setSizeKey] = useState("1:1");
  const [customWidth, setCustomWidth] = useState(1024);
  const [customHeight, setCustomHeight] = useState(1024);
  const [count, setCount] = useState(4);
  const [seedMode, setSeedMode] = useState<"random" | "fixed">("random");
  const [seedValue, setSeedValue] = useState(0);
  const [negative, setNegative] = useState("");
  const [position, setPosition] = useState("auto");
  const [composition, setComposition] = useState("");
  // "pack" = 팩 blend 설정 그대로 (오버라이드 안 함)
  const [blendMode, setBlendMode] = useState<"pack" | "overlay" | "imprint">("pack");
  const [logoRef, setLogoRef] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    api<{ packs: StylePackOption[]; size_presets: Record<string, number[]> }>("style-packs")
      .then((data) => {
        setPacks(data.packs);
        setPresets(data.size_presets);
      })
      .catch((e: Error) => setError(e.message));
    api<{ assets: AssetInfo[] }>("assets")
      .then((data) => setAssets(data.assets))
      .catch(() => setAssets([]));
    api<ModelsResponse>("models")
      .then((data) => {
        setModels(data.models);
        setEngineFlavor(data.engine_flavor ?? null);
        if (data.default) setModel((prev) => prev || data.default!);
      })
      .catch(() => setModels([]));
    fetchSamples().then(setSamples);
  }, []);

  useEffect(() => {
    if (!fromJobId) return;
    api<JobDetail>(`jobs/${fromJobId}`)
      .then(({ brief }) => {
        if (!brief) return;
        setCampaignText(brief.campaign_text);
        setObjectConcept(brief.object_concept);
        setModel(brief.model ?? "");
        setSelectedAssets(brief.assets ?? []);
        setSelectedPacks(brief.style.style_packs ?? []);
        setFreeText(brief.style.free_text ?? "");
        setRefs(brief.reference_images ?? []);
        if (typeof brief.size_preset === "string") {
          setSizeKey(brief.size_preset);
        } else {
          setSizeKey(CUSTOM_SIZE);
          setCustomWidth(brief.size_preset.width);
          setCustomHeight(brief.size_preset.height);
        }
        setCount(brief.candidate_count);
        if (brief.seed === "random") setSeedMode("random");
        else {
          setSeedMode("fixed");
          // 서버가 문자열로 내려주므로 숫자 입력란에 넣기 전에 변환한다
          setSeedValue(Number(brief.seed));
        }
        setNegative(brief.negative ?? "");
        setPosition(brief.placement_hint?.asset_position ?? "auto");
        setComposition(brief.placement_hint?.composition ?? "");
        setLogoRef(brief.logo_reference ?? "");
        setBlendMode(brief.asset_blend_mode ?? "pack");
      })
      .catch((e: Error) => setError(e.message));
  }, [fromJobId]);

  const currentModel = useMemo(() => models.find((m) => m.id === model), [models, model]);
  const consistencyWarning = selectedPacks.length > 1 || freeText.trim() !== "";
  const refsBlocked = currentModel ? !currentModel.supports_styleref : false;
  const logorefBlocked = currentModel ? !currentModel.supports_logoref : false;
  const conflict = selectedAssets.length > 0 && refs.length > 0;
  // 로고 참조(생성 트랙)는 자산 합성·스타일 참조와 동시 사용 불가 (v1 서버 제약)
  const logoConflict = logoRef !== "" && (selectedAssets.length > 0 || refs.length > 0);

  // 트랙을 못 쓰면 trimap으로 강등되지만 잡은 성공한다 — 차단하지 않고 품질 저하만 알린다.
  const translucentDowngrade =
    !!currentModel &&
    !currentModel.supports_native_alpha &&
    selectedPacks.some(
      (id) => packs.find((p) => p.id === id)?.material_class === "translucent",
    );

  function toggle(list: string[], value: string, set: (next: string[]) => void) {
    set(list.includes(value) ? list.filter((v) => v !== value) : [...list, value]);
  }

  async function uploadRef(file: File) {
    setRefBusy(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const { path } = await postForm<{ path: string }>("uploads", form);
      setRefs((prev) => (prev.includes(path) || prev.length >= 2 ? prev : [...prev, path]));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRefBusy(false);
    }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    // 생성 시작은 재차 확인을 받는다 — 잡 하나가 원격 GPU 과금·수 분 실행이고 비용은 후보 수에 비례한다.
    if (!confirm(`생성을 시작할까요? (후보 ${count}장)`)) return;
    setError(null);
    setSubmitting(true);
    const brief: BriefInput = {
      campaign_text: campaignText,
      object_concept: objectConcept,
      model: model || null,
      assets: selectedAssets,
      size_preset:
        sizeKey === CUSTOM_SIZE ? { width: customWidth, height: customHeight } : sizeKey,
      style: { style_packs: selectedPacks, free_text: freeText.trim() || null },
      candidate_count: count,
      seed: seedMode === "random" ? "random" : seedValue,
      placement_hint:
        selectedAssets.length > 0
          ? { asset_position: position, composition: composition.trim() || null }
          : null,
      negative: negative.trim() || null,
      reference_images: refs,
      logo_reference: logoRef || null,
      asset_blend_mode: blendMode === "pack" ? null : blendMode,
    };
    try {
      const { job_id } = await postJson<{ job_id: string }>("jobs", brief);
      router.push(`/history/${job_id}`);
    } catch (e) {
      setError((e as Error).message);
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <Section title="브리프">
        <div>
          <label className={label}>캠페인 텍스트</label>
          <p className={help}>컨셉 참고 전용 — 이미지에 렌더링되지 않습니다</p>
          <textarea
            value={campaignText}
            onChange={(e) => setCampaignText(e.target.value)}
            maxLength={500}
            required
            rows={2}
            className={`${field} mt-1.5`}
            placeholder="예: 여름 결산 세일 — 최대 70%"
          />
        </div>
        <div>
          <label className={label}>AI가 그릴 요소</label>
          <p className={help}>생성될 그래픽 오브젝트 설명 (예: 3D 쇼핑백과 % 기호 클러스터)</p>
          <input
            value={objectConcept}
            onChange={(e) => setObjectConcept(e.target.value)}
            maxLength={300}
            required
            className={`${field} mt-1.5`}
          />
        </div>
      </Section>

      <Section title="모델 · 스타일">
        {models.length > 0 && (
          <div>
            <div className="flex items-center gap-2">
              <label className={label}>생성 모델</label>
              {/* 어느 백엔드로 생성되는지 — 아래 제약 안내가 이 값에 따라 달라진다 */}
              {engineFlavor && (
                <span
                  className="rounded-full border border-[var(--line)] px-2 py-0.5 text-[11px] font-medium text-[var(--ink-soft)]"
                  title={
                    engineFlavor === "remote"
                      ? "원격 백엔드(GCP Cloud Run)에서 생성합니다"
                      : "로컬 ComfyUI에서 생성합니다"
                  }
                >
                  {engineFlavor === "remote" ? "원격 (GCP)" : "로컬"}
                </span>
              )}
            </div>
            <div className="mt-1.5 flex flex-wrap gap-2">
              {models.map((option) => (
                <Chip
                  key={option.id}
                  active={model === option.id}
                  onClick={() => setModel(option.id)}
                  title={
                    option.supports_styleref ? "참조 이미지 지원" : "참조 이미지 미지원"
                  }
                >
                  {option.label}
                </Chip>
              ))}
            </div>
          </div>
        )}
        <div>
          <label className={label}>스타일 팩 <span className="font-normal text-[var(--ink-soft)]">(선택 — 참조 이미지나 자유 텍스트만으로도 생성 가능)</span></label>
          <p className={help}>각 카드의 미리보기는 실제 산출 샘플입니다 — 하단 갤러리에서 더 볼 수 있습니다</p>
          <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
            {packs.map((pack) => {
              const active = selectedPacks.includes(pack.id);
              // 팩 썸네일은 로고 없는 순수 스타일 산출물만 — 참조·로고 트랙 샘플은 제외
              const isStyleSample = (s: SampleInfo) =>
                !["styleref", "logoref", "imprint"].includes(s.path);
              const thumb =
                samples.find(
                  (s) => s.pack === pack.id && s.model === model && isStyleSample(s),
                ) ?? samples.find((s) => s.pack === pack.id && isStyleSample(s));
              return (
                <button
                  type="button"
                  key={pack.id}
                  onClick={() => toggle(selectedPacks, pack.id, setSelectedPacks)}
                  title={`${pack.id} · v${pack.version} · shadow=${pack.shadow_policy}`}
                  className={`overflow-hidden rounded-md border text-left transition-colors ${
                    active
                      ? "border-[var(--mat)] shadow-[0_0_0_2px_var(--mat-tint)]"
                      : "border-[var(--line)] bg-white hover:border-[var(--mat)]/50"
                  }`}
                >
                  <span className="checker-fine flex h-20 w-full items-center justify-center p-1">
                    {thumb ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        src={sampleUrl(thumb.file)}
                        alt={`${pack.name} 샘플`}
                        loading="lazy"
                        className="max-h-full max-w-full object-contain"
                      />
                    ) : (
                      <span className="text-[10px] text-[var(--ink-soft)]/50">샘플 준비 중</span>
                    )}
                  </span>
                  <span
                    className={`block border-t px-2 py-1.5 ${
                      active ? "border-[var(--mat)]/30 bg-[var(--mat-tint)]" : "border-[var(--line)]"
                    }`}
                  >
                    <span className="block text-xs font-semibold">{pack.name}</span>
                    <span className="block font-mono text-[9px] text-[var(--ink-soft)]">
                      {pack.id}
                    </span>
                  </span>
                </button>
              );
            })}
          </div>
          <input
            value={freeText}
            onChange={(e) => setFreeText(e.target.value)}
            maxLength={500}
            placeholder="자유 스타일 텍스트 (선택 — 팩 위에 얹거나 단독 사용)"
            className={`${field} mt-2`}
          />
          {consistencyWarning && (
            <p className="mt-1.5 text-xs font-medium text-[var(--warn)]">
              팩 혼합·자유 입력 사용 — 스타일 일관성은 보증되지 않습니다
            </p>
          )}
          {translucentDowngrade && (
            <p className="mt-1.5 text-xs font-medium text-[var(--warn)]">
              반투명 전용 트랙(native alpha)을 쓸 수 없어 trimap으로 처리됩니다 — 유리·반투명
              재질의 알파가 덜 정확할 수 있습니다
            </p>
          )}
        </div>
        <div>
          <label className={label}>스타일 참조 이미지 (최대 2장)</label>
          <p className={help}>
            업로드한 이미지의 무드·질감을 반영해 생성합니다. 내용은 복제하지 않습니다.
          </p>
          {refsBlocked ? (
            <p className="mt-1.5 text-xs text-[var(--warn)]">
              선택한 모델은 참조 이미지를 지원하지 않습니다 — Qwen-Image를 선택하세요
            </p>
          ) : (
            <div className="mt-2 flex items-center gap-3">
              {refs.map((ref) => (
                <div key={ref} className="relative">
                  <div className="checker-fine flex h-16 w-16 items-center justify-center overflow-hidden rounded-md border border-[var(--line)]">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={uploadPreviewUrl(ref)}
                      alt="참조 이미지"
                      className="h-full w-full object-cover"
                    />
                  </div>
                  <button
                    type="button"
                    onClick={() => setRefs(refs.filter((r) => r !== ref))}
                    className="absolute -right-1.5 -top-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-[var(--ink)] text-[10px] text-white"
                    aria-label="참조 이미지 제거"
                  >
                    ×
                  </button>
                </div>
              ))}
              {refs.length < 2 && (
                <label className="flex h-16 w-16 cursor-pointer items-center justify-center rounded-md border border-dashed border-[var(--line)] text-xl text-[var(--ink-soft)] hover:border-[var(--mat)]">
                  {refBusy ? "…" : "+"}
                  <input
                    type="file"
                    accept="image/png,image/jpeg,image/webp"
                    className="hidden"
                    disabled={refBusy}
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) uploadRef(file);
                      e.target.value = "";
                    }}
                  />
                </label>
              )}
            </div>
          )}
        </div>
      </Section>

      <Section title="라이브러리 자산 합성">
        <p className={`${help} -mt-2`}>
          로고·오브젝트 자산은 <b>생성되지 않고 합성</b>됩니다(라이브러리 등록분만). 보존
          수준은 아래 합성 모드가 결정합니다 — 팩 기본/force는 원본 픽셀 100% 보존,
          overlay는 주변 조명·그림자만 정합(코어 보존), imprint는 표면 질감이 로고를
          관통해 색·명암이 변조됩니다(형상·hue는 상한 검증).
        </p>
        {assets.length === 0 ? (
          <p className="text-sm text-[var(--ink-soft)]">
            등록된 자산이 없습니다 —{" "}
            <a href="/assets" className="underline decoration-[var(--mat)] underline-offset-2">
              자산 페이지
            </a>
            에서 직접 등록할 수 있습니다
          </p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {assets.flatMap((asset) =>
              asset.variants.map((variant) => {
                const ref = `${asset.id}:${variant.id}`;
                const active = selectedAssets.includes(ref);
                return (
                  <button
                    type="button"
                    key={ref}
                    onClick={() => toggle(selectedAssets, ref, setSelectedAssets)}
                    className={`flex items-center gap-2 rounded-md border p-1.5 pr-3 text-sm transition-colors ${
                      active
                        ? "border-[var(--mat)] bg-[var(--mat-tint)]"
                        : "border-[var(--line)] bg-white hover:border-[var(--mat)]/50"
                    }`}
                  >
                    <span className="checker-fine block h-9 w-9 overflow-hidden rounded">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={assetPreviewUrl(asset.id, variant.id)}
                        alt=""
                        className="h-full w-full object-contain"
                      />
                    </span>
                    <span>
                      {asset.name}
                      <span className="ml-1 font-mono text-[10px] text-[var(--ink-soft)]">
                        {variant.id}
                      </span>
                    </span>
                  </button>
                );
              }),
            )}
          </div>
        )}
        {conflict && (
          <p className="text-xs font-medium text-[var(--fail)]">
            자산 삽입과 참조 이미지는 아직 동시에 쓸 수 없습니다 — 한쪽을 비워주세요
          </p>
        )}
        {selectedAssets.length > 0 && (
          <div className="flex gap-3">
            <select value={position} onChange={(e) => setPosition(e.target.value)} className={field}>
              {["auto", "center", "left", "right", "top", "bottom"].map((p) => (
                <option key={p} value={p}>
                  자산 위치: {p}
                </option>
              ))}
            </select>
            <input
              value={composition}
              onChange={(e) => setComposition(e.target.value)}
              maxLength={200}
              placeholder="구도 메모 (선택)"
              className={field}
            />
          </div>
        )}
        {selectedAssets.length > 0 && (
          <div>
            <label className={label}>합성 모드</label>
            <p className={help}>
              overlay는 조명·그림자를 맞춰 얹고, imprint는 표면 질감·음영이 로고를
              관통합니다(가죽·금속 새김). 기본은 스타일 팩의 blend 설정을 따릅니다.
            </p>
            <div className="mt-1.5 flex gap-2">
              {(
                [
                  ["pack", "팩 기본"],
                  ["overlay", "overlay — 조명·그림자"],
                  ["imprint", "imprint — 표면 새김"],
                ] as const
              ).map(([value, text]) => (
                <Chip key={value} active={blendMode === value} onClick={() => setBlendMode(value)}>
                  {text}
                </Chip>
              ))}
            </div>
          </div>
        )}
      </Section>

      <Section title="로고 참조 생성 (logoref)">
        <p className={`${help} -mt-2`}>
          로고를 <b>생성 단계에서 장면에 직접 각인</b>합니다 — 여러 표면·다양한 각도에
          자연스럽게 박히는 연출 컷용. 로고 픽셀 정확성은 보증되지 않으므로, 정확성이
          필요하면 위의 자산 삽입을 쓰세요. 자산 삽입·참조 이미지와 동시 사용은 불가합니다.
        </p>
        {logorefBlocked ? (
          <p className="text-xs text-[var(--warn)]">
            선택한 모델·백엔드는 로고 참조 생성을 지원하지 않습니다 — Qwen-Image를 선택하세요
          </p>
        ) : assets.length === 0 ? (
          <p className="text-sm text-[var(--ink-soft)]">등록된 자산이 없습니다</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {assets.flatMap((asset) =>
              asset.variants.map((variant) => {
                const ref = `${asset.id}:${variant.id}`;
                const active = logoRef === ref;
                return (
                  <button
                    type="button"
                    key={ref}
                    onClick={() => setLogoRef(active ? "" : ref)}
                    className={`flex items-center gap-2 rounded-md border p-1.5 pr-3 text-sm transition-colors ${
                      active
                        ? "border-[var(--mat)] bg-[var(--mat-tint)]"
                        : "border-[var(--line)] bg-white hover:border-[var(--mat)]/50"
                    }`}
                  >
                    <span className="checker-fine block h-9 w-9 overflow-hidden rounded">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={assetPreviewUrl(asset.id, variant.id)}
                        alt=""
                        className="h-full w-full object-contain"
                      />
                    </span>
                    <span>
                      {asset.name}
                      <span className="ml-1 font-mono text-[10px] text-[var(--ink-soft)]">
                        {variant.id}
                      </span>
                    </span>
                  </button>
                );
              }),
            )}
          </div>
        )}
        {logoConflict && (
          <p className="text-xs font-medium text-[var(--fail)]">
            로고 참조 생성은 자산 삽입·참조 이미지와 동시에 쓸 수 없습니다 — 한쪽을 비워주세요
          </p>
        )}
      </Section>

      <Section title="출력 설정">
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <div>
            <label className={label}>사이즈</label>
            <select
              value={sizeKey}
              onChange={(e) => setSizeKey(e.target.value)}
              className={`${field} mt-1.5`}
            >
              {Object.entries(presets).map(([key, [w, h]]) => (
                <option key={key} value={key}>
                  {key} · {w}×{h}
                </option>
              ))}
              <option value={CUSTOM_SIZE}>커스텀</option>
            </select>
            {sizeKey === CUSTOM_SIZE && (
              <div className="mt-1.5 flex gap-1.5">
                <input
                  type="number"
                  value={customWidth}
                  onChange={(e) => setCustomWidth(Number(e.target.value))}
                  min={256}
                  max={2048}
                  step={8}
                  className={field}
                  aria-label="가로"
                />
                <input
                  type="number"
                  value={customHeight}
                  onChange={(e) => setCustomHeight(Number(e.target.value))}
                  min={256}
                  max={2048}
                  step={8}
                  className={field}
                  aria-label="세로"
                />
              </div>
            )}
          </div>
          <div>
            <label className={label}>후보 수</label>
            <input
              type="number"
              value={count}
              onChange={(e) => setCount(Number(e.target.value))}
              min={1}
              max={8}
              className={`${field} mt-1.5`}
            />
          </div>
          <div>
            <label className={label}>시드</label>
            <select
              value={seedMode}
              onChange={(e) => setSeedMode(e.target.value as "random" | "fixed")}
              className={`${field} mt-1.5`}
            >
              <option value="random">랜덤</option>
              <option value="fixed">직접 입력</option>
            </select>
            {seedMode === "fixed" && (
              <input
                type="number"
                value={seedValue}
                onChange={(e) => setSeedValue(Number(e.target.value))}
                min={0}
                className={`${field} mt-1.5 font-mono`}
              />
            )}
          </div>
          <div>
            <label className={label}>금지 요소</label>
            <input
              value={negative}
              onChange={(e) => setNegative(e.target.value)}
              maxLength={500}
              placeholder="예: 텍스트, 손"
              className={`${field} mt-1.5`}
            />
          </div>
        </div>
      </Section>

      {error && (
        <p className="rounded-md bg-[var(--fail-tint)] px-4 py-3 text-sm text-[var(--fail)]">
          {error}
        </p>
      )}
      <button
        disabled={submitting || conflict || logoConflict}
        className="rounded-md bg-[var(--mat)] px-6 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-[var(--mat-deep)] disabled:opacity-40"
      >
        {submitting ? "제출 중…" : "생성 시작"}
      </button>
    </form>
  );
}
