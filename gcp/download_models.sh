#!/usr/bin/env bash
# remote_models.tsv 기준으로 로컬에 없는 원격 필요 모델만 Hugging Face에서 내려받는다.
# 이미 있는 파일은 건드리지 않는다 — 로컬 보유본이 항상 우선이다.
#
# 사용법: ./download_models.sh [--check] LOCAL_MODELS_DIR
#   --check           받을 파일 계획만 출력하고 종료한다 (비대화형 허용, 다운로드 없음)
#   LOCAL_MODELS_DIR  로컬 ComfyUI models 디렉토리 (예: ~/AI/ComfyUI/models)
#
# 환경 변수:
#   HF_TOKEN  Hugging Face 토큰. 게이트(라이선스 동의 필요) 리포일 때만 필요하다.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

readonly TSV="$SCRIPT_DIR/remote_models.tsv"

log() {
    printf '%s [download-models] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2
}

fail() {
    log "치명적 오류: $*"
    exit 1
}

usage() {
    printf '사용법: %s [--check] LOCAL_MODELS_DIR\n' "$0" >&2
    printf '  --check           받을 파일 계획만 출력하고 종료 (다운로드 없음)\n' >&2
    printf '  LOCAL_MODELS_DIR  로컬 ComfyUI models 디렉토리\n' >&2
    printf '환경 변수: HF_TOKEN — 게이트 리포일 때만 필요\n' >&2
}

check_only=0
MODELS_DIR=""
while (( $# > 0 )); do
    case "$1" in
        --check) check_only=1 ;;
        -h|--help) usage; exit 0 ;;
        -*) usage; fail "알 수 없는 옵션: $1" ;;
        *)
            if [[ -n "$MODELS_DIR" ]]; then
                usage
                fail "LOCAL_MODELS_DIR 인자가 중복됐다"
            fi
            MODELS_DIR="$1"
            ;;
    esac
    shift
done

if [[ -z "$MODELS_DIR" ]]; then
    usage
    fail "LOCAL_MODELS_DIR 인자가 필요하다"
fi
[[ -d "$MODELS_DIR" ]] || fail "로컬 모델 디렉토리가 없다: $MODELS_DIR"
[[ -f "$TSV" ]] || fail "모델 목록 파일이 없다: $TSV"
command -v curl >/dev/null 2>&1 || fail "curl을 찾지 못했다"

# sha256 도구 — macOS는 shasum, 리눅스는 sha256sum이 기본이다.
sha256_of() {
    if command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    elif command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    else
        # 도구가 없으면 빈 값을 돌려주고 호출자가 핀 검증 불가로 처리한다
        printf ''
    fi
}

auth_args=()
if [[ -n "${HF_TOKEN:-}" ]]; then
    auth_args=(-H "Authorization: Bearer $HF_TOKEN")
fi

log "=== STEP 1: 계획 수립 (보유 파일은 건너뛴다) ==="

plan_rel=()
plan_url=()
plan_sha=()
plan_dest=()
have_count=0

while IFS=$'\t' read -r rel url sha; do
    [[ -z "$rel" || "$rel" == \#* ]] && continue
    if src="$(pm_local_model_source "$MODELS_DIR" "$rel")"; then
        log "보유: $rel  ($src)"
        have_count=$(( have_count + 1 ))
        continue
    fi
    dest_dir="$(pm_local_model_dest_dir "$MODELS_DIR" "$rel")"
    plan_rel+=("$rel")
    plan_url+=("$url")
    plan_sha+=("$sha")
    plan_dest+=("$dest_dir/${rel#*/}")
done < "$TSV"

if (( ${#plan_rel[@]} == 0 )); then
    log "원격 필요 모델 ${have_count}개를 전부 보유하고 있다. 내려받을 것이 없다."
    exit 0
fi

log "받을 파일 ${#plan_rel[@]}개:"
for i in "${!plan_rel[@]}"; do
    url="${plan_url[$i]}"
    # HEAD로 실재·크기·접근 권한을 미리 확인한다 — 수십 GB를 받다가 실패하는 것보다 낫다
    code="$(curl -sIL -o /dev/null -w '%{http_code}' --max-time 30 ${auth_args[@]+"${auth_args[@]}"} "$url" || printf '000')"
    size="$(curl -sIL --max-time 30 ${auth_args[@]+"${auth_args[@]}"} "$url" 2>/dev/null | grep -i '^content-length' | tail -1 | tr -d '\r' | awk '{print $2}')"
    size_h="?"
    if [[ -n "$size" ]]; then
        size_h="$(awk -v b="$size" 'BEGIN { printf "%.1fGB", b / 1073741824 }')"
    fi
    log "  - ${plan_rel[$i]}  (${size_h}, HTTP ${code}) → ${plan_dest[$i]}"
    case "$code" in
        200) ;;
        401|403)
            if [[ -n "${HF_TOKEN:-}" ]]; then
                fail "접근 거부(${code}): $url — HF_TOKEN 계정으로 해당 리포의 라이선스에 동의했는지 확인하라"
            fi
            fail "접근 거부(${code}): $url — 게이트 리포다. HF_TOKEN 환경 변수를 설정하고 다시 실행하라"
            ;;
        *) fail "다운로드 사전 확인 실패(HTTP ${code}): $url — remote_models.tsv의 URL을 점검하라" ;;
    esac
done

if (( check_only )); then
    log "--check 모드 — 여기서 종료한다"
    exit 0
fi

if [[ ! -t 0 ]]; then
    fail "비대화형 실행이라 확인 프롬프트를 표시할 수 없다. 계획만 보려면 --check를 쓰라"
fi
printf '위 파일들을 내려받는다 (이어받기 지원). 계속하려면 yes를 입력하라: ' >&2
read -r confirmation
if [[ "$confirmation" != "yes" ]]; then
    fail "사용자가 취소했다"
fi

log "=== STEP 2: 다운로드 ==="

for i in "${!plan_rel[@]}"; do
    rel="${plan_rel[$i]}"
    url="${plan_url[$i]}"
    pin="${plan_sha[$i]}"
    dest="${plan_dest[$i]}"
    part="${dest}.part"

    mkdir -p "$(dirname "$dest")"
    log "받는 중: $rel → $dest"
    # --continue-at - : 중단됐던 .part에서 이어받는다. --fail: HTTP 오류를 실패로 처리.
    curl -L --fail --retry 3 --retry-delay 5 --continue-at - \
        ${auth_args[@]+"${auth_args[@]}"} \
        -o "$part" "$url"
    mv "$part" "$dest"

    actual="$(sha256_of "$dest")"
    if [[ "$pin" != "-" && -n "$pin" ]]; then
        if [[ -z "$actual" ]]; then
            fail "sha256 도구가 없어 핀 검증을 못 한다: $rel (shasum 또는 sha256sum 설치 필요)"
        fi
        if [[ "$actual" != "$pin" ]]; then
            mv "$dest" "${dest}.sha-mismatch"
            fail "sha256 불일치: $rel — 기대 $pin, 실제 $actual (${dest}.sha-mismatch로 옮겨 둠)"
        fi
        log "sha256 일치: $rel"
    else
        log "sha256: ${actual:-계산 불가}  ($rel)"
        log "  핀 미설정 — remote_models.tsv와 model_profiles.yaml remote 블록에 이 값을 핀하라"
    fi
done

log "=== 완료 ==="
log "다음 단계: ./sync_models.sh PROJECT_ID \"$MODELS_DIR\" 로 버킷에 업로드하라"
