// 잡 상태 → 진행률(%) — 대기열 패널·사이드바·리뷰 페이지가 공유하는 단일 규칙

export type PendingState = "queued" | "preparing" | "running" | "postprocess";

export function jobProgressPercent(state: string, done: number, total: number): number {
  switch (state) {
    case "queued":
      return 2;
    case "preparing":
      return 8;
    case "running":
      return total > 0 ? 10 + Math.round((done / total) * 80) : 45;
    case "postprocess":
      return 92;
    case "done":
      return 100;
    default:
      return 0;
  }
}

export type EngineStatus = {
  reachable: boolean;
  busy: boolean;
  running: number;
  pending: number;
  external: number; // 웹 잡(pm_*)이 아닌 엔진 큐 항목 수 — CLI 배치 등
};

export type QueueJob = {
  job_id: string;
  state: PendingState;
  done: number;
  total: number;
  campaign_text: string;
  object_concept: string;
  free_text?: string | null;
  style_packs?: string[];
  model?: string | null;
  seed?: number | string | "random" | null;
  candidate_count?: number | null;
  queue_position?: number;
};

export type QueueSnapshot = {
  running: QueueJob | null;
  queued: QueueJob[];
  engine?: EngineStatus;
};

export const QUEUE_STATE_LABEL: Record<PendingState, string> = {
  queued: "큐 대기",
  preparing: "엔진 준비 중",
  running: "생성 중",
  postprocess: "후처리·QA",
};
