#!/usr/bin/env bash
# 로컬 ComfyUI 모델 디렉토리 → gs://PROJECT_ID-models 단방향 업로드.
#
# ── 방향은 로컬 → GCS 뿐이다 ──────────────────────────────────────────────────
# 역방향(GCS → 로컬) 동기화는 이 스크립트에 없고, 추가해서도 안 된다.
# 모델 세트가 100GB 단위라 한 번 내려받는 것만으로 egress 요금이 $17 이상 발생한다.
# 로컬이 원본이고 버킷은 사본이다. 원격에서 복구할 일이 생기면 비용을 인지한 상태에서
# gcloud storage cp를 직접 실행하라.
#
# 올릴 파일 목록의 단일 소스는 remote_models.tsv다 — 전체 미러링이 아니다.
# 원격 qwen은 L4(24GB)에 맞춘 Q6_K 변형을 쓰고 로컬은 Q8을 유지하기 때문이다.
# 로컬에 없는 파일은 ./fetch_models_remote.sh(GCP 직행, 권장) 또는 ./download_models.sh로 확보한다.
# 이미 버킷에 있는 파일은 로컬에 없어도 통과한다.
#
# 사용법: ./sync_models.sh [PROJECT_ID] LOCAL_MODELS_DIR
#   예:  ./sync_models.sh ~/AI/ComfyUI/models              (프로젝트는 격리 구성 기본값)
#        ./sync_models.sh my-project ~/AI/ComfyUI/models
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

readonly TSV="$SCRIPT_DIR/remote_models.tsv"

STAGING_DIR=""

log() {
    printf '%s [sync-models] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2
}

fail() {
    log "치명적 오류: $*"
    exit 1
}

cleanup() {
    if [[ -n "$STAGING_DIR" && -d "$STAGING_DIR" ]]; then
        rm -rf "$STAGING_DIR"
    fi
}
trap cleanup EXIT

usage() {
    printf '사용법: %s [PROJECT_ID] LOCAL_MODELS_DIR\n' "$0" >&2
    printf '  PROJECT_ID        GCP 프로젝트 ID (생략 시 격리 구성의 기본 프로젝트, 버킷은 PROJECT_ID-models)\n' >&2
    printf '  LOCAL_MODELS_DIR  로컬 ComfyUI models 디렉토리\n' >&2
}

if (( $# < 1 || $# > 2 )); then
    usage
    fail "인자는 [PROJECT_ID] LOCAL_MODELS_DIR — 1개 또는 2개다"
fi

if (( $# == 2 )); then
    PROJECT_ID="$1"
    LOCAL_MODELS_DIR="$2"
else
    PROJECT_ID=""
    LOCAL_MODELS_DIR="$1"
fi
readonly LOCAL_MODELS_DIR
# 인자를 바꿔 넣으면 로컬 경로 자리에 gs:// URI가 온다 — 역방향 실행을 여기서 막는다.
if [[ "$LOCAL_MODELS_DIR" == gs://* ]]; then
    fail "LOCAL_MODELS_DIR에 gs:// URI가 왔다. 이 스크립트는 로컬 → GCS 단방향 전용이다"
fi
if [[ ! -d "$LOCAL_MODELS_DIR" ]]; then
    fail "로컬 모델 디렉토리가 없다: $LOCAL_MODELS_DIR"
fi
[[ -f "$TSV" ]] || fail "모델 목록 파일이 없다: $TSV"

command -v gcloud >/dev/null 2>&1 || fail "gcloud CLI를 찾지 못했다"
active_account="$(require_auth)" || exit 1

if [[ -z "$PROJECT_ID" ]]; then
    if ! PROJECT_ID="$(pm_default_project)"; then
        usage
        fail "PROJECT_ID가 없다 — 인자로 주거나 gcp/login.sh <PROJECT_ID>로 격리 구성에 설정하라"
    fi
    log "PROJECT_ID 생략 — 격리 구성의 기본 프로젝트를 쓴다: $PROJECT_ID"
fi
readonly PROJECT_ID

readonly BUCKET_URI="gs://${PROJECT_ID}-models"

log "=== STEP 1: 대상 파일 확인 ==="

STAGING_DIR="$(mktemp -d)"
missing_files=""
staged_count=0
in_bucket_count=0

# 버킷 경로(rel) 기준으로 스테이징한다. 로컬 원본 위치는 배치가 달라도
# (clip/ ↔ text_encoders/, unet/ ↔ diffusion_models/) pm_local_model_source가 찾는다.
while IFS=$'\t' read -r rel _url _sha; do
    [[ -z "$rel" || "$rel" == \#* ]] && continue
    if ! src="$(pm_local_model_source "$LOCAL_MODELS_DIR" "$rel")"; then
        # 원격 전용 파일은 fetch_models_remote.sh가 버킷에 직접 넣는다 — 그 경우 로컬 부재는 정상이다.
        if gcloud storage objects describe "$BUCKET_URI/$rel" --project "$PROJECT_ID" >/dev/null 2>&1; then
            log "로컬에 없지만 버킷에 이미 있다 — 건너뜀: $rel"
            in_bucket_count=$(( in_bucket_count + 1 ))
        else
            missing_files="${missing_files}  - ${rel}
"
        fi
        continue
    fi
    mkdir -p "$STAGING_DIR/$(dirname "$rel")"
    # 하드링크로 스테이징한다 — 수십 GB를 복사하지 않기 위해서다.
    # 로컬 모델이 다른 파일시스템(외장 볼륨 등)에 있으면 링크가 실패하므로 복사로 되돌린다.
    if ! ln "$src" "$STAGING_DIR/$rel" 2>/dev/null; then
        log "하드링크 불가 — 복사로 스테이징한다: $rel"
        cp "$src" "$STAGING_DIR/$rel"
    fi
    staged_count=$(( staged_count + 1 ))
done < "$TSV"

if [[ -n "$missing_files" ]]; then
    printf '없는 파일 (로컬에도 버킷에도 없음):\n%s' "$missing_files" >&2
    fail "./fetch_models_remote.sh \"$LOCAL_MODELS_DIR\" (GCP 직행, 권장) 또는 ./download_models.sh \"$LOCAL_MODELS_DIR\" 로 먼저 확보하라"
fi

if (( staged_count == 0 )); then
    log "올릴 파일이 없다 — 전부 버킷에 이미 있다 (${in_bucket_count}개 확인)."
    exit 0
fi

upload_size="$(du -sh "$STAGING_DIR" | cut -f1)"

log "계정       : $active_account (격리 구성: $CLOUDSDK_CONFIG)"
log "프로젝트   : $PROJECT_ID"
log "로컬 원본  : $LOCAL_MODELS_DIR"
log "업로드 대상: $BUCKET_URI  (${staged_count}개 파일, 약 ${upload_size})"
log "방향       : 로컬 → GCS 단방향 (원격 파일 삭제 안 함)"

if [[ ! -t 0 ]]; then
    fail "비대화형 실행이라 확인 프롬프트를 표시할 수 없다. 이 스크립트는 사람이 직접 실행한다"
fi
printf '위 내용으로 업로드를 진행한다. 계속하려면 yes를 입력하라: ' >&2
read -r confirmation
if [[ "$confirmation" != "yes" ]]; then
    fail "사용자가 취소했다"
fi

log "=== STEP 2: 업로드 (gcloud storage rsync) ==="
# --delete-unmatched-destination-objects는 일부러 넣지 않는다.
# 버킷에 다른 파일이 있어도 지우지 않는다(사고 방지).
gcloud storage rsync "$STAGING_DIR" "$BUCKET_URI" \
    --project "$PROJECT_ID" \
    --recursive

log "=== 완료 ==="
log "확인: gcloud storage ls -r \"$BUCKET_URI\" --project \"$PROJECT_ID\""
