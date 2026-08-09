#!/usr/bin/env bash
# Cloud Run 컨테이너 진입점.
#   1) 인증 프록시(nginx)를 $PORT에 띄우고 Bearer 토큰이 맞을 때만 127.0.0.1:8188로 넘긴다
#   2) ComfyUI를 127.0.0.1:8188에 띄운다 (외부 직결 불가)
#   3) 자기 종료 와치독 — 유휴가 확인되면 컨테이너를 스스로 내려 GPU 과금을 멈춘다
#
# 유휴 꼬리 과금(요청 종료 후 최대 10분 전액 과금)을 ~90초로 줄이는 것이 3)의 목적이다.
set -euo pipefail

# launchd/Cloud Run 모두 최소 PATH로 실행될 수 있어 명시한다.
export PATH="/opt/venv/bin:/usr/local/nvidia/bin:/usr/local/cuda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

readonly COMFY_HOME="${COMFY_HOME:-/opt/ComfyUI}"
readonly PM_HOME="${PM_HOME:-/opt/pm}"
readonly PYTHON_BIN="${PM_PYTHON_BIN:-/opt/venv/bin/python}"
readonly COMFY_HOST="127.0.0.1"
readonly COMFY_PORT="8188"
readonly MODELS_DIR="${PM_MODELS_DIR:-/models}"
readonly EXTRA_MODEL_PATHS="${PM_EXTRA_MODEL_PATHS:-$PM_HOME/extra_model_paths.yaml}"
readonly NGINX_TEMPLATE="$PM_HOME/nginx.conf.template"

# Cloud Run이 $PORT를 주입한다(기본 8080). 상태 포트는 루프백 전용이라 노출되지 않는다.
readonly PROXY_PORT="${PORT:-8080}"
readonly STATUS_PORT="${PM_STATUS_PORT:-8189}"

# 와치독 파라미터. PM_IDLE_EXIT_SECONDS=0이면 자기 종료를 끄고 프로세스 감시만 한다.
readonly IDLE_EXIT_SECONDS="${PM_IDLE_EXIT_SECONDS:-90}"
readonly MIN_UPTIME_SECONDS="${PM_MIN_UPTIME_SECONDS:-120}"
readonly WATCHDOG_INTERVAL_SECONDS="${PM_WATCHDOG_INTERVAL_SECONDS:-15}"
readonly SHUTDOWN_GRACE_SECONDS="${PM_SHUTDOWN_GRACE_SECONDS:-20}"

RUNTIME_DIR=""
ACCESS_LOG=""
NGINX_PID=""
COMFY_PID=""

log() {
    printf '%s [pm-entrypoint] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2
}

fail() {
    log "치명적 오류: $*"
    exit 1
}

cleanup() {
    # 렌더된 nginx 설정에는 프록시 토큰이 들어 있다. 종료 시 반드시 지운다.
    if [[ -n "$RUNTIME_DIR" && -d "$RUNTIME_DIR" ]]; then
        rm -rf "$RUNTIME_DIR"
    fi
}
trap cleanup EXIT

process_alive() {
    local pid="$1"
    [[ -n "$pid" ]] || return 1
    kill -0 "$pid" 2>/dev/null
}

# 두 자식 프로세스를 정상 종료시킨다. nginx를 먼저 내려 새 요청 유입을 끊는다.
shutdown_children() {
    local pid
    local waited=0

    for pid in "$NGINX_PID" "$COMFY_PID"; do
        [[ -n "$pid" ]] || continue
        # 이미 죽은 프로세스면 kill이 실패한다 — 그 경우 목표는 이미 달성된 상태다.
        kill -TERM "$pid" 2>/dev/null || true
    done

    while (( waited < SHUTDOWN_GRACE_SECONDS )); do
        if ! process_alive "$NGINX_PID" && ! process_alive "$COMFY_PID"; then
            return 0
        fi
        sleep 1
        waited=$(( waited + 1 ))
    done

    log "정상 종료 대기 ${SHUTDOWN_GRACE_SECONDS}초 초과 — 강제 종료한다"
    for pid in "$NGINX_PID" "$COMFY_PID"; do
        [[ -n "$pid" ]] || continue
        kill -KILL "$pid" 2>/dev/null || true
    done
}

on_termination_signal() {
    log "종료 시그널 수신 (Cloud Run 인스턴스 회수) — 자식 프로세스를 내린다"
    shutdown_children
    exit 0
}
trap on_termination_signal TERM INT

require_non_negative_integer() {
    local name="$1"
    local value="$2"
    if ! [[ "$value" =~ ^[0-9]+$ ]]; then
        fail "$name 값이 0 이상의 정수가 아니다: $value"
    fi
}

comfy_is_ready() {
    curl -fsS --max-time 5 -o /dev/null "http://${COMFY_HOST}:${COMFY_PORT}/system_stats"
}

# 0: 큐 비어 있음 / 1: 실행·대기 작업 있음 / 2: 판정 불가
# 판정 불가를 "비어 있음"으로 뭉개면 생성 중인 잡을 죽일 수 있어 코드를 분리한다.
comfy_queue_state() {
    local body
    body="$(curl -fsS --max-time 5 "http://${COMFY_HOST}:${COMFY_PORT}/queue" 2>/dev/null)" || return 2
    "$PYTHON_BIN" -c '
import json
import sys

try:
    data = json.loads(sys.stdin.read())
except ValueError:
    sys.exit(2)
if not isinstance(data, dict):
    sys.exit(2)
running = data.get("queue_running") or []
pending = data.get("queue_pending") or []
sys.exit(0 if not running and not pending else 1)
' <<<"$body"
}

# 0: 처리 중인 프록시 요청 없음 / 1: 있음 / 2: 판정 불가
# 액세스 로그는 요청 "완료" 시점에 기록되므로, 콜드 스타트 중 진행 중인 첫 업로드나
# 결과 다운로드는 로그에 나타나지 않는다. stub_status로 그 구멍을 막는다.
proxy_inflight_state() {
    local body
    local writing
    body="$(curl -fsS --max-time 5 "http://127.0.0.1:${STATUS_PORT}/pm-nginx-status" 2>/dev/null)" || return 2
    writing="$(printf '%s\n' "$body" | sed -n 's/.*Writing: \([0-9][0-9]*\).*/\1/p')"
    [[ -n "$writing" ]] || return 2
    # 이 상태 조회 자체가 Writing 1건으로 잡힌다.
    (( writing <= 1 ))
}

seconds_since_last_request() {
    local mtime
    local now
    mtime="$(stat -c %Y "$ACCESS_LOG" 2>/dev/null)" || return 1
    now="$(date +%s)"
    printf '%s\n' $(( now - mtime ))
}

log "=== STEP 1: 환경 검증 ==="

if [[ -z "${PM_PROXY_TOKEN:-}" ]]; then
    fail "PM_PROXY_TOKEN이 비어 있다 — Secret Manager(pm-backend-token) 주입을 확인하라"
fi
# 토큰은 nginx 설정에 문자열로 들어가므로 설정 문법을 깨는 문자를 미리 막는다.
# 값은 어떤 경로로도 로깅하지 않는다.
# 길이는 정규식 반복({16,512})으로 재지 않는다 — POSIX RE_DUP_MAX가 플랫폼마다 달라(BSD는 255)
# 정규식 컴파일이 실패하면 정상 토큰까지 거부된다. 문자 집합과 길이를 따로 본다.
if ! [[ "$PM_PROXY_TOKEN" =~ ^[A-Za-z0-9._+/=-]+$ ]]; then
    fail "PM_PROXY_TOKEN에 허용되지 않는 문자가 있다 (허용: A-Za-z0-9 . _ + / = -)"
fi
if (( ${#PM_PROXY_TOKEN} < 16 || ${#PM_PROXY_TOKEN} > 512 )); then
    fail "PM_PROXY_TOKEN 길이가 허용 범위를 벗어났다 (16~512자, 실제 ${#PM_PROXY_TOKEN}자)"
fi

require_non_negative_integer "PORT" "$PROXY_PORT"
require_non_negative_integer "PM_STATUS_PORT" "$STATUS_PORT"
require_non_negative_integer "PM_IDLE_EXIT_SECONDS" "$IDLE_EXIT_SECONDS"
require_non_negative_integer "PM_MIN_UPTIME_SECONDS" "$MIN_UPTIME_SECONDS"
require_non_negative_integer "PM_WATCHDOG_INTERVAL_SECONDS" "$WATCHDOG_INTERVAL_SECONDS"
require_non_negative_integer "PM_SHUTDOWN_GRACE_SECONDS" "$SHUTDOWN_GRACE_SECONDS"

if (( WATCHDOG_INTERVAL_SECONDS < 1 )); then
    fail "PM_WATCHDOG_INTERVAL_SECONDS는 1 이상이어야 한다: $WATCHDOG_INTERVAL_SECONDS"
fi
# 같은 포트면 nginx가 두 server 블록을 바인드하다 실패한다. 원인을 알기 어려운 에러라 먼저 끊는다.
if (( PROXY_PORT == STATUS_PORT )); then
    fail "PORT와 PM_STATUS_PORT가 같다($PROXY_PORT). 와치독 상태 포트를 다른 값으로 바꿔라"
fi
if [[ ! -x "$PYTHON_BIN" ]]; then
    fail "Python 실행 파일이 없다: $PYTHON_BIN"
fi
if [[ ! -f "$COMFY_HOME/main.py" ]]; then
    fail "ComfyUI 설치를 찾지 못했다: $COMFY_HOME/main.py"
fi
if [[ ! -r "$NGINX_TEMPLATE" ]]; then
    fail "nginx 템플릿을 읽을 수 없다: $NGINX_TEMPLATE"
fi
if [[ ! -r "$EXTRA_MODEL_PATHS" ]]; then
    fail "extra_model_paths 설정을 읽을 수 없다: $EXTRA_MODEL_PATHS"
fi
# 모델은 GCS FUSE 마운트에서만 온다. 마운트가 없으면 생성이 전부 실패하므로 여기서 끊는다.
if [[ ! -d "$MODELS_DIR" ]]; then
    fail "모델 마운트 경로가 없다: $MODELS_DIR (Cloud Run GCS FUSE 볼륨 설정을 확인하라)"
fi

log "=== STEP 2: 인증 프록시 설정 렌더 ==="

umask 077
RUNTIME_DIR="$(mktemp -d)"
# nginx 워커(www-data)가 하위 디렉토리에 닿아야 하므로 통과 권한만 연다.
# 디렉토리 목록·설정 파일 읽기는 여전히 root 전용이다.
chmod 0711 "$RUNTIME_DIR"
mkdir -p \
    "$RUNTIME_DIR/body" \
    "$RUNTIME_DIR/proxy" \
    "$RUNTIME_DIR/fastcgi" \
    "$RUNTIME_DIR/uwsgi" \
    "$RUNTIME_DIR/scgi"
chown www-data:www-data \
    "$RUNTIME_DIR/body" \
    "$RUNTIME_DIR/proxy" \
    "$RUNTIME_DIR/fastcgi" \
    "$RUNTIME_DIR/uwsgi" \
    "$RUNTIME_DIR/scgi"

ACCESS_LOG="$RUNTIME_DIR/access.log"
: > "$ACCESS_LOG"
chown www-data:www-data "$ACCESS_LOG"
chmod 0640 "$ACCESS_LOG"

NGINX_CONF="$RUNTIME_DIR/nginx.conf"
# envsubst에 SHELL-FORMAT을 넘겨 지정한 5개만 치환한다.
# 그러지 않으면 $http_authorization 같은 nginx 변수까지 빈 문자열로 날아간다.
# shellcheck disable=SC2016  # 작은따옴표가 맞다 — SHELL-FORMAT은 셸이 아니라 envsubst가 해석한다
PM_PORT="$PROXY_PORT" \
PM_RUNTIME_DIR="$RUNTIME_DIR" \
PM_ACCESS_LOG="$ACCESS_LOG" \
PM_STATUS_PORT="$STATUS_PORT" \
PM_PROXY_TOKEN="$PM_PROXY_TOKEN" \
    envsubst '${PM_PORT} ${PM_RUNTIME_DIR} ${PM_ACCESS_LOG} ${PM_STATUS_PORT} ${PM_PROXY_TOKEN}' \
    < "$NGINX_TEMPLATE" > "$NGINX_CONF"
chmod 0600 "$NGINX_CONF"

# nginx -t는 결과만 출력하고 설정 본문은 찍지 않는다 — 토큰이 로그로 새지 않는다.
if ! nginx -t -c "$NGINX_CONF"; then
    fail "nginx 설정 검증 실패 (렌더된 설정은 토큰 때문에 출력하지 않는다)"
fi

log "=== STEP 3: 인증 프록시 기동 (:${PROXY_PORT} → ${COMFY_HOST}:${COMFY_PORT}) ==="
nginx -c "$NGINX_CONF" &
NGINX_PID=$!

log "=== STEP 4: ComfyUI 기동 (${COMFY_HOST}:${COMFY_PORT}, 모델 마운트 ${MODELS_DIR}) ==="
cd "$COMFY_HOME"
# 모델이 네트워크 파일시스템(GCS FUSE) 위에 있어 적재 관련 기본값을 뒤집는다.
#   --disable-dynamic-vram : 기본 지연 적재는 가중치 조각마다 GCS 왕복을 만든다. --highvram·
#                            --gpu-only도 끄지만 L4(22.5GB)에 unet+TE 동시 상주로 OOM 위험이 있다.
#   --disable-mmap은 넣지 말 것 — TE 읽기가 16배 느려진다(89 MB/s → 5.2 MB/s).
#                            mmap 경로가 순차 읽기라 마운트의 미리 읽기 최적화를 받는다).
"$PYTHON_BIN" -u main.py \
    --listen "$COMFY_HOST" \
    --port "$COMFY_PORT" \
    --extra-model-paths-config "$EXTRA_MODEL_PATHS" \
    --disable-dynamic-vram \
    --disable-auto-launch &
COMFY_PID=$!

log "=== STEP 5: 와치독 (주기 ${WATCHDOG_INTERVAL_SECONDS}초 / 유휴 종료 ${IDLE_EXIT_SECONDS}초 / 최소 가동 ${MIN_UPTIME_SECONDS}초) ==="
if (( IDLE_EXIT_SECONDS == 0 )); then
    log "PM_IDLE_EXIT_SECONDS=0 — 자기 종료 비활성. 프로세스 생존 감시만 수행한다"
fi

started_at="$(date +%s)"
ready_marked=0

while true; do
    # sleep을 백그라운드로 두고 wait해야 SIGTERM이 대기 중에도 즉시 트랩으로 간다.
    sleep "$WATCHDOG_INTERVAL_SECONDS" &
    sleep_pid=$!
    # 시그널로 중단되면 0이 아닌 코드가 온다. 처리는 트랩이 맡으므로 여기서는 무시한다.
    wait "$sleep_pid" || true

    # 어느 한쪽이 죽으면 나머지를 살려둘 이유가 없다 — 좀비 인스턴스가 과금만 발생시킨다.
    if ! process_alive "$NGINX_PID"; then
        log "nginx 프로세스가 사라졌다 — 컨테이너 전체를 내린다"
        shutdown_children
        exit 1
    fi
    if ! process_alive "$COMFY_PID"; then
        log "ComfyUI 프로세스가 사라졌다 — 컨테이너 전체를 내린다"
        shutdown_children
        exit 1
    fi

    if (( ready_marked == 0 )) && comfy_is_ready; then
        : > "$RUNTIME_DIR/comfy_ready"
        ready_marked=1
        log "ComfyUI 준비 완료 — /pm-healthz가 200을 반환한다"
    fi

    (( IDLE_EXIT_SECONDS > 0 )) || continue

    now="$(date +%s)"
    uptime_seconds=$(( now - started_at ))
    (( uptime_seconds > MIN_UPTIME_SECONDS )) || continue

    queue_state=0
    comfy_queue_state || queue_state=$?
    if (( queue_state == 2 )); then
        log "경고: ComfyUI /queue를 읽지 못했다 — 이번 주기의 유휴 판정을 보류한다"
        continue
    fi
    (( queue_state == 0 )) || continue

    inflight_state=0
    proxy_inflight_state || inflight_state=$?
    if (( inflight_state == 2 )); then
        log "경고: nginx 상태를 읽지 못했다 — 이번 주기의 유휴 판정을 보류한다"
        continue
    fi
    (( inflight_state == 0 )) || continue

    if ! idle_seconds="$(seconds_since_last_request)"; then
        log "경고: 액세스 로그 mtime을 읽지 못했다 — 이번 주기의 유휴 판정을 보류한다"
        continue
    fi
    (( idle_seconds >= IDLE_EXIT_SECONDS )) || continue

    log "유휴 확인 (큐 비었음 / 처리 중 요청 없음 / 마지막 요청 후 ${idle_seconds}초 / 가동 ${uptime_seconds}초)"
    shutdown_children
    log "정상 종료 — Cloud Run 인스턴스가 회수되며 GPU 과금이 멈춘다"
    exit 0
done
