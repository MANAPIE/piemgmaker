#!/usr/bin/env bash
# PIEmgmaker 전용 gcloud 인증 — 최초 1회 실행.
# 전역 gcloud 상태와 무관한 전용 디렉토리(CLOUDSDK_CONFIG)에 자격 증명과 ADC를 만든다.
# 이후 이 저장소의 모든 스크립트·Make 타깃은 자동으로 이 격리 환경을 쓴다.
#
# 사용법: ./login.sh [PROJECT_ID]
#   PROJECT_ID를 주면 전용 구성의 기본 프로젝트로 설정한다.
#   프로젝트를 아직 안 만들었으면(bootstrap 전) 생략하고, 나중에 다시 실행해도 된다.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

log() {
    printf '%s [login] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2
}

fail() {
    log "치명적 오류: $*"
    exit 1
}

command -v gcloud >/dev/null 2>&1 || fail "gcloud CLI를 찾지 못했다"

PROJECT_ID="${1:-}"

log "=== STEP 1: 전용 gcloud 홈 준비 ==="
mkdir -p "$CLOUDSDK_CONFIG"
log "격리 디렉토리: $CLOUDSDK_CONFIG"
log "전역 gcloud 구성·ADC는 건드리지 않는다. 되돌리려면 이 디렉토리를 지우면 끝이다."

log "=== STEP 2: 계정 로그인 (브라우저) ==="
log "주의: 브라우저에서 반드시 이 프로젝트에 쓸 계정을 선택하라"
gcloud auth login

log "=== STEP 3: ADC 발급 — terraform용 (브라우저, 같은 계정 선택) ==="
gcloud auth application-default login

if [[ -n "$PROJECT_ID" ]]; then
    log "=== STEP 4: 기본 프로젝트 설정 ==="
    gcloud config set project "$PROJECT_ID"
    if gcloud projects describe "$PROJECT_ID" >/dev/null 2>&1; then
        # 사용자 ADC는 일부 API 호출에 quota project를 요구한다.
        gcloud auth application-default set-quota-project "$PROJECT_ID" \
            || log "quota project 설정 실패 — bootstrap.sh 이후 이 스크립트를 다시 실행하면 된다"
    else
        log "프로젝트가 아직 없어 quota project 설정을 건너뛴다 (bootstrap.sh 뒤 재실행 가능)"
    fi
fi

log "=== 완료 ==="
active_account="$(require_auth)" || fail "로그인이 완료되지 않았다"
log "계정      : $active_account"
log "ADC 파일  : $GOOGLE_APPLICATION_CREDENTIALS"
if project="$(pm_default_project)"; then
    log "기본 프로젝트: $project — 이후 bootstrap·make·deploy·sync에서 PROJECT_ID 인자를 생략할 수 있다"
else
    log "팁: ./login.sh <PROJECT_ID>로 기본 프로젝트를 설정하면 이후 모든 명령에서 PROJECT_ID 인자를 생략할 수 있다"
fi
log "다음 단계 : infra/bootstrap.sh  (기본 프로젝트·단일 결제 계정이면 인자 생략 가능)"
# 결제 계정 목록은 bootstrap 인자 준비용 힌트다 — 권한·API 문제로 실패해도 로그인 결과와 무관하다.
log "결제 계정 목록:"
gcloud billing accounts list 2>/dev/null || log "(조회 실패 — 콘솔에서 확인하거나 bootstrap 단계에서 지정하라)"
