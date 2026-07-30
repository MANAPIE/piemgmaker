#!/usr/bin/env bash
# ComfyUI 이미지를 Cloud Build로 빌드해 Artifact Registry에 올리고,
# Cloud Run 두 서비스(pm-comfy-qwen / pm-comfy-flux2)의 이미지를 갱신한다.
#
# 베이스 인프라(AR 리포·서비스·버킷·SA·시크릿)는 Terraform 소유다.
# 이 스크립트는 "이미지 롤아웃"만 소유한다 — TF는 run 서비스의 image 변경을 무시하도록 설정돼 있다.
#
# 사용법: ./deploy.sh [PROJECT_ID] [--initial]
#   PROJECT_ID 생략 시 격리 구성의 기본 프로젝트를 쓴다 (gcp/login.sh <PROJECT_ID>로 설정).
#   --initial  최초 1회용 — Cloud Run 서비스가 아직 없는 상태에서 이미지만 빌드·push한다.
#              run 서비스는 실제로 기동하는 이미지가 있어야 생성되므로(startup probe)
#              infra/run apply보다 먼저 실행한다. 태그는 bootstrap 고정이라
#              run/terraform.tfvars의 qwen_image·flux2_image에 출력된 URI를 그대로 쓴다.
set -euo pipefail

readonly REGION="asia-southeast1"
readonly AR_REPO="pm-comfy"
readonly IMAGE_NAME="pm-comfy"
readonly SERVICES="pm-comfy-qwen pm-comfy-flux2"

# Cloud Build 기본 머신. gcloud가 허용하는 값은 e2-medium / e2-highcpu-8 / e2-highcpu-32 /
# n1-highcpu-8 / n1-highcpu-32 뿐이고, 무료 티어(일 120분)가 적용되는 기본 풀 머신은 e2-medium이다.
readonly BUILD_MACHINE_TYPE="${PM_BUILD_MACHINE_TYPE:-e2-medium}"
# torch(cu128) 휠과 매팅 가중치를 받으므로 기본 10분으로는 부족하다.
readonly BUILD_TIMEOUT="${PM_BUILD_TIMEOUT:-3600s}"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

log() {
    printf '%s [deploy] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2
}

fail() {
    log "치명적 오류: $*"
    exit 1
}

usage() {
    printf '사용법: %s [PROJECT_ID] [--initial]\n' "$0" >&2
    printf '  PROJECT_ID  배포 대상 GCP 프로젝트 ID (생략 시 격리 구성의 기본 프로젝트)\n' >&2
    printf '  --initial   최초 1회 — 서비스 존재 확인·갱신 없이 이미지만 빌드·push (태그 bootstrap)\n' >&2
    printf '환경 변수:\n' >&2
    printf '  PM_IMAGE_TAG            이미지 태그 (기본: UTC 타임스탬프-git단축해시, --initial은 bootstrap)\n' >&2
    printf '  PM_BUILD_MACHINE_TYPE   Cloud Build 머신 (기본: %s)\n' "$BUILD_MACHINE_TYPE" >&2
    printf '  PM_BUILD_TIMEOUT        Cloud Build 타임아웃 (기본: %s)\n' "$BUILD_TIMEOUT" >&2
}

PROJECT_ID=""
initial=0
while (( $# > 0 )); do
    case "$1" in
        --initial) initial=1 ;;
        -h|--help) usage; exit 0 ;;
        -*) usage; fail "알 수 없는 옵션: $1" ;;
        *)
            if [[ -n "$PROJECT_ID" ]]; then
                usage
                fail "PROJECT_ID 인자가 중복됐다"
            fi
            PROJECT_ID="$1"
            ;;
    esac
    shift
done

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

image_tag="${PM_IMAGE_TAG:-}"
if [[ -z "$image_tag" ]]; then
    if (( initial )); then
        # 최초 이미지는 run/terraform.tfvars가 참조하는 고정 태그다.
        image_tag="bootstrap"
    else
        # git 저장소가 아니어도 배포는 가능하다 — 태그에 리비전을 붙이지 못할 뿐이다.
        revision="$(git -C "$SCRIPT_DIR" rev-parse --short HEAD 2>/dev/null || true)"
        if [[ -n "$revision" ]]; then
            image_tag="$(date -u +%Y%m%d-%H%M%S)-${revision}"
        else
            image_tag="$(date -u +%Y%m%d-%H%M%S)"
        fi
    fi
fi
readonly image_tag

readonly IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/${IMAGE_NAME}:${image_tag}"

log "=== STEP 1: 사전 점검 ==="

if ! gcloud artifacts repositories describe "$AR_REPO" \
        --project "$PROJECT_ID" --location "$REGION" >/dev/null 2>&1; then
    fail "Artifact Registry 리포 '$AR_REPO'가 없다 ($REGION). infra/base terraform apply를 먼저 실행하라"
fi

if (( ! initial )); then
    for service in $SERVICES; do
        if ! gcloud run services describe "$service" \
                --project "$PROJECT_ID" --region "$REGION" >/dev/null 2>&1; then
            fail "Cloud Run 서비스 '$service'가 없다 ($REGION). 최초 구축이면 --initial로 이미지를 먼저 올리고 infra/run apply를 하라"
        fi
    done
fi

if [[ ! -f "$SCRIPT_DIR/Dockerfile" ]]; then
    fail "Dockerfile을 찾지 못했다: $SCRIPT_DIR/Dockerfile"
fi

log "계정       : $active_account (격리 구성: $CLOUDSDK_CONFIG)"
log "프로젝트   : $PROJECT_ID"
log "리전       : $REGION"
log "이미지     : $IMAGE_URI"
log "빌드 머신  : $BUILD_MACHINE_TYPE (타임아웃 $BUILD_TIMEOUT)"
if (( initial )); then
    log "모드       : --initial (이미지 빌드·push만, 서비스 갱신 없음)"
else
    log "갱신 대상  : $SERVICES"
fi

if [[ ! -t 0 ]]; then
    fail "비대화형 실행이라 확인 프롬프트를 표시할 수 없다. 이 스크립트는 사람이 직접 실행한다"
fi
printf '위 내용으로 빌드·배포를 진행한다. 계속하려면 yes를 입력하라: ' >&2
read -r confirmation
if [[ "$confirmation" != "yes" ]]; then
    fail "사용자가 취소했다"
fi

log "=== STEP 2: Cloud Build 이미지 빌드 및 AR 푸시 ==="
gcloud builds submit "$SCRIPT_DIR" \
    --project "$PROJECT_ID" \
    --region "$REGION" \
    --tag "$IMAGE_URI" \
    --machine-type "$BUILD_MACHINE_TYPE" \
    --timeout "$BUILD_TIMEOUT"

if (( initial )); then
    log "=== 완료 (--initial) ==="
    log "빌드된 이미지: $IMAGE_URI"
    log "다음 단계: infra/run/terraform.tfvars의 qwen_image·flux2_image에 위 URI를 넣고"
    log "           make run-init / run-plan / run-apply PROJECT_ID=$PROJECT_ID 를 실행하라"
    exit 0
fi

log "=== STEP 3: Cloud Run 서비스 이미지 갱신 ==="
for service in $SERVICES; do
    log "갱신 중: $service"
    gcloud run services update "$service" \
        --project "$PROJECT_ID" \
        --region "$REGION" \
        --image "$IMAGE_URI"
done

log "=== 완료 ==="
log "배포된 이미지: $IMAGE_URI"
log "스모크: PM_BACKEND_AUTH 토큰으로 각 서비스 URL의 /system_stats를 호출해 200을 확인하라"
