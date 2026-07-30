# shellcheck shell=bash
# PIEmgmaker 전용 gcloud 환경 — 이 저장소의 모든 GCP 도구가 source하는 계정 격리 레이어.
#
# CLOUDSDK_CONFIG를 전용 디렉토리로 돌리면 gcloud의 자격 증명·구성·ADC가 통째로 그 안에
# 격리된다. 머신의 전역 gcloud(다른 프로젝트·계정)와 서로 영향을 주고받지 않으므로,
# 어떤 계정이 전역에 활성이든 이 저장소의 도구는 항상 전용 계정으로만 동작한다.
#
# 재정의는 PM_GCLOUD_CONFIG_DIR 하나로만 한다. infra/Makefile도 같은 기본값을 쓴다 —
# 바꾸려면 양쪽을 함께 바꿔야 한다.
export CLOUDSDK_CONFIG="${PM_GCLOUD_CONFIG_DIR:-$HOME/.config/piemgmaker/gcloud}"

# terraform(google provider)은 gcloud 활성 계정이 아니라 ADC 파일을 읽는다.
# gcloud auth application-default login이 CLOUDSDK_CONFIG 안에 만드는 파일을 가리켜
# terraform까지 같은 격리 계정을 쓰게 한다. (전역 ~/.config/gcloud ADC는 건드리지 않는다)
export GOOGLE_APPLICATION_CREDENTIALS="$CLOUDSDK_CONFIG/application_default_credentials.json"

# 전용 저장소에 활성 계정이 있는지 확인하고 stdout으로 계정을 돌려준다.
# 없으면 안내 후 실패한다 — 격리를 모르는 채 전역 계정으로 실행되는 사고를 막는 가드다.
require_auth() {
    local account
    account="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null || true)"
    if [[ -z "$account" ]]; then
        printf 'PIEmgmaker 전용 gcloud 인증이 없다 (%s).\n' "$CLOUDSDK_CONFIG" >&2
        printf '최초 1회 gcp/login.sh를 실행해 이 프로젝트에 쓸 계정으로 로그인하라.\n' >&2
        return 1
    fi
    printf '%s' "$account"
}

# 격리 구성에 설정된 기본 프로젝트를 돌려준다. 미설정이면 실패(1).
# login.sh <PROJECT_ID>(또는 gcloud config set project)로 한 번 설정해 두면
# bootstrap·Makefile·deploy·sync가 PROJECT_ID 인자를 생략할 수 있다.
pm_default_project() {
    local project
    project="$(gcloud config get-value project 2>/dev/null || true)"
    if [[ -z "$project" || "$project" == "(unset)" ]]; then
        return 1
    fi
    printf '%s' "$project"
}

# open 상태 결제 계정이 정확히 1개일 때만 그 ID를 돌려준다. 0개거나 여럿이면 실패(1) —
# 어느 계정에 과금이 연결될지 모호한 상황에서 조용히 고르는 일을 막는다.
pm_default_billing_account() {
    local accounts count
    accounts="$(gcloud billing accounts list --filter='open=true' --format='value(name)' 2>/dev/null || true)"
    if [[ -z "$accounts" ]]; then
        return 1
    fi
    count="$(printf '%s\n' "$accounts" | wc -l | tr -d ' ')"
    if [[ "$count" != "1" ]]; then
        return 1
    fi
    printf '%s' "$accounts"
}

# 버킷 상대 경로(rel)의 로컬 원본 파일을 찾는다. ComfyUI 로컬 배치는 버킷 레이아웃과
# 다를 수 있다 — clip/은 text_encoders/에, unet/은 diffusion_models/에 있을 수 있어
# 순서대로 탐색한다. 찾으면 절대 경로를 출력하고 0, 없으면 1을 반환한다.
pm_local_model_source() {
    local models_dir="$1" rel="$2"
    local top="${rel%%/*}" rest="${rel#*/}"
    local candidates=("$top")
    case "$top" in
        clip) candidates+=("text_encoders") ;;
        unet) candidates+=("diffusion_models") ;;
    esac
    local dir
    for dir in "${candidates[@]}"; do
        if [[ -f "$models_dir/$dir/$rest" ]]; then
            printf '%s' "$models_dir/$dir/$rest"
            return 0
        fi
    done
    return 1
}

# 버킷 상대 경로(rel)를 내려받을 로컬 목적지 디렉토리를 고른다.
# 이미 같은 계열 파일이 들어 있는 후보 디렉토리를 우선한다 (기존 배치와의 일관성),
# 다음은 존재하는 디렉토리, 둘 다 없으면 첫 후보를 그대로 쓴다 (호출자가 mkdir).
pm_local_model_dest_dir() {
    local models_dir="$1" rel="$2"
    local top="${rel%%/*}"
    local candidates=("$top")
    case "$top" in
        clip) candidates+=("text_encoders") ;;
        unet) candidates+=("diffusion_models") ;;
    esac
    local dir
    for dir in "${candidates[@]}"; do
        if [[ -d "$models_dir/$dir" ]] && [[ -n "$(ls -A "$models_dir/$dir" 2>/dev/null)" ]]; then
            printf '%s' "$models_dir/$dir"
            return 0
        fi
    done
    for dir in "${candidates[@]}"; do
        if [[ -d "$models_dir/$dir" ]]; then
            printf '%s' "$models_dir/$dir"
            return 0
        fi
    done
    printf '%s' "$models_dir/${candidates[0]}"
}
