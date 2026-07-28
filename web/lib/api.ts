// API 타입드 클라이언트 — Python pydantic 스키마와 수동 동기

export type SizeSpec = { width: number; height: number };

export type BriefInput = {
  campaign_text: string;
  object_concept: string;
  model?: string | null;
  assets: string[];
  size_preset: string | SizeSpec;
  style: { style_packs: string[]; free_text?: string | null };
  candidate_count: number;
  seed: "random" | number;
  placement_hint?: { asset_position: string; composition?: string | null } | null;
  negative?: string | null;
  reference_images?: string[];
};

export type QACheck = {
  rule: string;
  passed: boolean;
  measured?: number | null;
  detail?: string;
  regen_hint?: string | null;
};

export type Candidate = {
  index: number;
  seed: number;
  passed: boolean;
  checks: QACheck[];
  final: string | null;
  candidate: string | null;
};

export type JobState =
  | "queued"
  | "preparing"
  | "running"
  | "postprocess"
  | "done"
  | "failed"
  | "canceled";

export type JobDetail = {
  job_id: string;
  state: JobState;
  done: number;
  total: number;
  error: string | null;
  passed: boolean | null;
  strategy: string | null;
  created_at: number | null;
  queue_position: number | null;
  brief: BriefInput | null;
  pins: {
    workflow: { id: string; hash: string } | null;
    style_packs: { id: string; version: string }[] | null;
    model_id: string | null;
    matting_chain: string[] | null;
  };
  candidates: Candidate[];
  selection: number[] | null;
  rerun_of: string | null;
};

export type HistoryItem = {
  job_id: string;
  created_at: number | null;
  state: JobState;
  passed: boolean | null;
  campaign_text: string;
  object_concept: string;
  packs: string[];
  thumb: string | null;
  selected: boolean;
};

export type HistoryPage = { items: HistoryItem[]; total: number; page: number; page_size: number };

export type StylePackOption = {
  id: string;
  name: string;
  version: string;
  material_class: "opaque" | "translucent";
  shadow_policy: string;
};

export type SampleInfo = {
  id: string;
  file: string;
  model: string;
  model_label: string;
  pack: string;
  subject: string;
  seed: number;
  path: string; // segment | trimap | native_alpha | styleref
};

export async function fetchSamples(): Promise<SampleInfo[]> {
  // 정적 public이 아니라 API 서빙 — 빌드 이후 추가된 샘플도 즉시 반영된다
  try {
    const data = await api<{ samples?: SampleInfo[] }>("samples");
    return data.samples ?? [];
  } catch {
    return [];
  }
}

export function sampleUrl(file: string): string {
  return `/api/pm/samples/${file}`;
}

export type ModelOption = {
  id: string;
  label: string;
  supports_styleref: boolean;
  supports_inpaint: boolean;
  supports_native_alpha: boolean;
};

export type AssetVariantInfo = { id: string; archived: boolean; tags: string[] };

export type AssetInfo = {
  id: string;
  name: string;
  type: "logo" | "object";
  archived: boolean;
  variants: AssetVariantInfo[];
  usage: { min_scale: number; clear_space_px: number };
};

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/pm/${path}`, init);
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: string;
      error?: string;
    } | null;
    throw new Error(body?.detail ?? body?.error ?? `요청 실패 (${response.status})`);
  }
  return (await response.json()) as T;
}

export function postJson<T>(path: string, body: unknown): Promise<T> {
  return api<T>(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function postForm<T>(path: string, form: FormData): Promise<T> {
  return api<T>(path, { method: "POST", body: form });
}

export function imageUrl(jobId: string, rel: string): string {
  return `/api/pm/jobs/${jobId}/images/${rel}`;
}

export function assetPreviewUrl(assetId: string, variantId: string): string {
  return `/api/pm/assets/${assetId}/preview/${variantId}.png`;
}

export function uploadPreviewUrl(refPath: string): string {
  // 참조 이미지 경로(서버 파일 경로)에서 파일명만 뽑아 서빙 라우트로
  const name = refPath.split("/").pop() ?? refPath;
  return `/api/pm/uploads/${name}`;
}
