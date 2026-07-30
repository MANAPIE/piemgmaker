#!/usr/bin/env bash
# 원격 전용 모델을 Mac을 거치지 않고 GCP 안에서 HF → 모델 버킷으로 직행시킨다.
# 다운로드는 HF Xet 클라이언트(hf download)를 쓴다 — plain HTTP는 후반부 스로틀이 실측됐다.
# Cloud Build VM(같은 리전)이 다운로드·sha256 검증·업로드를 수행한다 —
# GCP 대역폭이라 수 분이면 끝나고, 로컬 회선으로는 1바이트도 오가지 않는다.
#
# 대상 선정: 기본은 「버킷에 없고 + 로컬에도 없는」 파일만 (로컬 보유분은 sync_models.sh 소관).
# --all이면 로컬 보유 여부와 무관하게 버킷에 없는 파일 전부를 HF에서 가져온다 —
# tsv의 sha256 핀을 전송 시 검증하므로 로컬 업로드 없이 버킷을 완성할 수 있다.
#
# 사용법: ./fetch_models_remote.sh [--check] [--all] [PROJECT_ID] LOCAL_MODELS_DIR
#   --check  가져올 파일 계획만 출력하고 종료 (비대화형 허용)
#   --all    로컬 보유 파일도 HF에서 직행 (로컬 업로드 생략 목적)
# 비용: 빌드 머신 e2-highcpu-8은 무료 티어 밖(전체 ~$1~2). ingress·동일 리전 쓰기는 무료.
# HF 토큰: hf-token 시크릿이 있으면 자동 인증 — 익명은 수 GB 후 스로틀되므로 사실상 필수다.
set -euo pipefail

readonly REGION="asia-southeast1"
# HF CDN이 이 경로에 ~10MiB/s 수준 제한을 거는 것이 실측됐다 — 30GB급 파일이 60분을 넘길 수 있어 기본을 3시간으로 둔다.
readonly BUILD_TIMEOUT="${PM_FETCH_TIMEOUT:-10800s}"
# e2-medium(RAM 4GB)은 Xet 병렬 다운로드 중 메모리 소진으로 VM이 정지하는 것이 실측됐다.
# e2-highcpu-8(8vCPU/8GB)은 무료 티어 밖이지만 파일당 ~10분 × 수 센트 수준이다.
readonly BUILD_MACHINE="${PM_FETCH_MACHINE_TYPE:-e2-highcpu-8}"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

readonly TSV="$SCRIPT_DIR/remote_models.tsv"

CONFIG_FILE=""

log() {
    printf '%s [fetch-remote] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2
}

fail() {
    log "치명적 오류: $*"
    exit 1
}

cleanup() {
    if [[ -n "$CONFIG_FILE" && -f "$CONFIG_FILE" ]]; then
        rm -f "$CONFIG_FILE"
    fi
}
trap cleanup EXIT

usage() {
    printf '사용법: %s [--check] [--all] [PROJECT_ID] LOCAL_MODELS_DIR\n' "$0" >&2
    printf '  --check           가져올 파일 계획만 출력하고 종료\n' >&2
    printf '  --all             로컬 보유 파일도 HF에서 버킷으로 직행 (로컬 업로드 생략)\n' >&2
    printf '  PROJECT_ID        GCP 프로젝트 ID (생략 시 격리 구성의 기본 프로젝트)\n' >&2
    printf '  LOCAL_MODELS_DIR  로컬 ComfyUI models 디렉토리\n' >&2
}

check_only=0
fetch_all=0
positional=()
while (( $# > 0 )); do
    case "$1" in
        --check) check_only=1 ;;
        --all) fetch_all=1 ;;
        -h|--help) usage; exit 0 ;;
        -*) usage; fail "알 수 없는 옵션: $1" ;;
        *) positional+=("$1") ;;
    esac
    shift
done

case "${#positional[@]}" in
    1) PROJECT_ID=""; MODELS_DIR="${positional[0]}" ;;
    2) PROJECT_ID="${positional[0]}"; MODELS_DIR="${positional[1]}" ;;
    *) usage; fail "인자는 [PROJECT_ID] LOCAL_MODELS_DIR — 1개 또는 2개다" ;;
esac

[[ -d "$MODELS_DIR" ]] || fail "로컬 모델 디렉토리가 없다: $MODELS_DIR"
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

# HF 토큰(무료 계정, read 권한)이 있으면 인증 다운로드로 — 익명 요청은 HF가 수 GB 후
# KiB/s로 조이는 것이 실측됐다. 토큰은 Secret Manager의 hf-token 시크릿에서 읽어
# 빌드 단계 env로만 주입한다 (설정 파일·로그에 값이 남지 않는다).
readonly HF_SECRET="hf-token"
hf_secret_version=""
if gcloud secrets describe "$HF_SECRET" --project "$PROJECT_ID" >/dev/null 2>&1; then
    project_number="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
    build_sa="${project_number}-compute@developer.gserviceaccount.com"
    # 이 시크릿 하나에 한정된 접근 부여 — 반복 실행해도 무해(멱등)
    gcloud secrets add-iam-policy-binding "$HF_SECRET" --project "$PROJECT_ID" \
        --member "serviceAccount:${build_sa}" --role roles/secretmanager.secretAccessor >/dev/null
    hf_secret_version="projects/${PROJECT_ID}/secrets/${HF_SECRET}/versions/latest"
    log "HF 토큰 시크릿 감지 — 인증 다운로드로 진행한다 (익명 대비 높은 속도 한도)"
else
    log "경고: HF 토큰 시크릿(${HF_SECRET})이 없다 — 익명 다운로드는 수 GB 후 심하게 느려진다."
    log "  huggingface.co/settings/tokens 에서 read 토큰을 만들고 아래로 저장하면 자동 사용된다:"
    log "  printf '%s' '<hf_토큰값>' | gcloud secrets create ${HF_SECRET} --data-file=- --project ${PROJECT_ID}"
fi

if (( fetch_all )); then
    log "=== STEP 1: 대상 선정 (--all — 버킷에 없는 파일 전부) ==="
else
    log "=== STEP 1: 대상 선정 (버킷·로컬 둘 다 없는 파일만) ==="
fi

plan_rel=()
plan_url=()
plan_sha=()

while IFS=$'\t' read -r rel url sha; do
    [[ -z "$rel" || "$rel" == \#* ]] && continue
    if src="$(pm_local_model_source "$MODELS_DIR" "$rel")" && (( ! fetch_all )); then
        log "로컬 보유 — sync_models.sh 소관: $rel  ($src)"
        continue
    fi
    if gcloud storage objects describe "$BUCKET_URI/$rel" --project "$PROJECT_ID" >/dev/null 2>&1; then
        log "버킷에 이미 있음 — 건너뜀: $rel"
        continue
    fi
    plan_rel+=("$rel")
    plan_url+=("$url")
    plan_sha+=("$sha")
done < "$TSV"

if (( ${#plan_rel[@]} == 0 )); then
    log "가져올 파일이 없다 — 전부 로컬 또는 버킷에 있다."
    exit 0
fi

log "계정   : $active_account (격리 구성: $CLOUDSDK_CONFIG)"
log "대상   : $BUCKET_URI  (Cloud Build 리전: $REGION)"
log "가져올 파일 ${#plan_rel[@]}개:"
for i in "${!plan_rel[@]}"; do
    log "  - ${plan_rel[$i]}  ← ${plan_url[$i]}"
done

if (( check_only )); then
    log "--check 모드 — 여기서 종료한다"
    exit 0
fi

if [[ ! -t 0 ]]; then
    fail "비대화형 실행이라 확인 프롬프트를 표시할 수 없다. 계획만 보려면 --check를 쓰라"
fi
printf 'GCP 안에서 위 파일들을 버킷으로 직행시킨다 (Cloud Build). 계속하려면 yes를 입력하라: ' >&2
read -r confirmation
if [[ "$confirmation" != "yes" ]]; then
    fail "사용자가 취소했다"
fi

# 파일별로 빌드 1회 — 다운로드 → sha256 → (핀 있으면 대조) → 업로드.
# 빌드 VM 무료 디스크(100GB)에 받았다가 올리므로 스트리밍 실패·재시도에 안전하다.
# 업로드 403이 나면: 빌드 SA의 버킷 쓰기 권한은 infra/base(models_build_writer)가 부여한다 —
#   make -C infra base-apply 를 먼저 실행하라.
for i in "${!plan_rel[@]}"; do
    rel="${plan_rel[$i]}"
    url="${plan_url[$i]}"
    pin="${plan_sha[$i]}"

    log "=== STEP 2: 서버사이드 전송 — $rel ==="
    # tsv의 URL에서 HF repo와 파일 경로를 뽑는다 — hf CLI는 URL이 아니라 repo/경로를 받는다.
    if [[ "$url" != https://huggingface.co/*/resolve/main/* ]]; then
        fail "HF resolve URL 형식이 아니다: $url (서버사이드 fetch는 HF만 지원한다)"
    fi
    stripped="${url#https://huggingface.co/}"
    repo_id="${stripped%%/resolve/*}"
    file_path="${stripped#*/resolve/main/}"
    secret_step_line=""
    secret_block=""
    if [[ -n "$hf_secret_version" ]]; then
        secret_step_line="    secretEnv: ['HF_TOKEN']"
        secret_block="availableSecrets:
  secretManager:
    - versionName: ${hf_secret_version}
      env: 'HF_TOKEN'"
    fi
    CONFIG_FILE="$(mktemp)"
    cat > "$CONFIG_FILE" <<EOF
steps:
  - name: gcr.io/google.com/cloudsdktool/cloud-sdk:slim
    entrypoint: bash
${secret_step_line}
    args:
      - -ceu
      - |
        # HF 전용 Xet 클라이언트로 받는다 — plain HTTP(curl·aria2)는 대용량 후반부(~80%)에
        # KiB/s로 스로틀되는 것이 실측됐다. Xet(CAS 청크) 경로는 그 제한을 우회한다.
        apt-get -qq update >/dev/null && apt-get -qq install -y python3-pip python3-venv >/dev/null
        python3 -m venv /tmp/hfenv
        /tmp/hfenv/bin/pip install --quiet "huggingface_hub[hf_xet]"
        # HF_HOME을 /workspace로 — 청크 캐시까지 du에 잡혀 진행률이 보인다.
        # HF_XET_HIGH_PERFORMANCE는 쓰지 않는다 — 시작 직후 대량 버퍼 할당으로 8GB 머신에서도
        # OOM(exit 137)이 실측됐다. 인증 토큰이 주 속도 레버라 기본 모드로도 충분히 빠르다.
        export HF_HOME=/workspace/hfhome HF_HUB_DISABLE_PROGRESS_BARS=1
        echo "다운로드 시작(hf-xet): ${repo_id} :: ${file_path}"
        ( while sleep 15; do echo "진행: \$(du -sm /workspace 2>/dev/null | cut -f1)MB 수신"; done ) &
        watcher=\$!
        /tmp/hfenv/bin/hf download '${repo_id}' '${file_path}' --local-dir /workspace/dl
        kill "\$watcher" 2>/dev/null || true
        mv "/workspace/dl/${file_path}" /workspace/model.bin
        echo "다운로드 완료: \$(du -m /workspace/model.bin | cut -f1)MB — sha256 계산 중"
        actual="\$(sha256sum /workspace/model.bin | cut -d' ' -f1)"
        echo "sha256: \$actual"
        if [ '${pin}' != '-' ] && [ "\$actual" != '${pin}' ]; then
          echo "sha256 불일치 — 기대 ${pin}" >&2
          exit 1
        fi
        gcloud storage cp /workspace/model.bin '${BUCKET_URI}/${rel}'
timeout: ${BUILD_TIMEOUT}
${secret_block}
EOF
    gcloud builds submit \
        --no-source \
        --config "$CONFIG_FILE" \
        --project "$PROJECT_ID" \
        --region "$REGION" \
        --machine-type "$BUILD_MACHINE"
    rm -f "$CONFIG_FILE"
    CONFIG_FILE=""
    log "완료: $BUCKET_URI/$rel"
    if [[ "$pin" == "-" ]]; then
        log "  핀 미설정 — 빌드 로그의 sha256 값을 remote_models.tsv와 model_profiles.yaml remote 블록에 핀하라"
    fi
done

log "=== 완료 ==="
log "확인: gcloud storage ls -l \"$BUCKET_URI/unet/\" --project \"$PROJECT_ID\""