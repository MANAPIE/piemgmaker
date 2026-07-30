#!/usr/bin/env bash
# PIEmgmaker GCP 프로젝트 최초 1회 부트스트랩.
# Terraform이 다룰 수 없는 선행 조건만 만든다 — 프로젝트 자체, 결제 연결, API 활성화, tfstate 버킷.
# 그 다음부터는 infra/Makefile의 terraform 타깃이 인프라를 소유한다.
#
# 사용법: ./bootstrap.sh [PROJECT_ID] [BILLING_ACCOUNT]
#   PROJECT_ID       생략 시 격리 구성의 기본 프로젝트 (gcp/login.sh <PROJECT_ID>로 설정)
#   BILLING_ACCOUNT  생략 시 open 결제 계정이 정확히 1개면 자동 감지
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# 계정 격리 — 전역 gcloud가 아니라 전용 구성(CLOUDSDK_CONFIG)으로만 동작한다.
# shellcheck source=../gcp/env.sh
source "$SCRIPT_DIR/../gcp/env.sh"

REGION="${REGION:-asia-southeast1}"

REQUIRED_APIS=(
  run.googleapis.com
  artifactregistry.googleapis.com
  cloudbuild.googleapis.com
  secretmanager.googleapis.com
  storage.googleapis.com
  cloudbilling.googleapis.com
  billingbudgets.googleapis.com
  monitoring.googleapis.com
  logging.googleapis.com
  # Direct VPC egress용 VPC·서브넷을 base root가 만든다. GPU 서비스가 Cloud Storage에서
  # 모델을 적재할 때 Google이 Direct VPC + Private Google Access를 요구사항으로 명시한다.
  compute.googleapis.com
  # 아래 3종은 terraform provider가 이 프로젝트를 quota project로 쓸 때(user_project_override)
  # API 호출이 여기로 귀속되므로 필요하다 — 꺼져 있으면 403 SERVICE_DISABLED.
  iam.googleapis.com
  cloudresourcemanager.googleapis.com
  serviceusage.googleapis.com
)

usage() {
  cat >&2 <<'EOF'
사용법: ./bootstrap.sh [PROJECT_ID] [BILLING_ACCOUNT]

  PROJECT_ID       새로 만들 GCP 프로젝트 ID (전역 유일, 소문자·숫자·하이픈)
                   생략 시 격리 구성의 기본 프로젝트를 쓴다 — gcp/login.sh <PROJECT_ID>로 설정
  BILLING_ACCOUNT  연결할 결제 계정 ID (gcloud billing accounts list로 확인)
                   생략 시 open 결제 계정이 정확히 1개면 그 계정을 쓴다

환경 변수:
  REGION           tfstate 버킷 리전 (기본 asia-southeast1)
EOF
}

if [[ $# -gt 2 ]]; then
  usage
  exit 2
fi

PROJECT_ID="${1:-}"
BILLING_ACCOUNT="${2:-}"

GCLOUD="$(command -v gcloud || true)"
if [[ -z "$GCLOUD" ]]; then
  echo "gcloud CLI를 찾을 수 없습니다. Google Cloud SDK를 설치한 뒤 다시 실행하세요." >&2
  exit 1
fi

if ! ACTIVE_ACCOUNT="$(require_auth)"; then
  exit 1
fi

if [[ -z "$PROJECT_ID" ]]; then
  if ! PROJECT_ID="$(pm_default_project)"; then
    usage
    echo "PROJECT_ID가 없습니다 — 인자로 주거나 gcp/login.sh <PROJECT_ID>로 격리 구성에 설정하세요." >&2
    exit 2
  fi
  echo "PROJECT_ID 생략 — 격리 구성의 기본 프로젝트를 씁니다: ${PROJECT_ID}"
fi

if [[ -z "$BILLING_ACCOUNT" ]]; then
  if ! BILLING_ACCOUNT="$(pm_default_billing_account)"; then
    usage
    echo "결제 계정을 자동으로 정할 수 없습니다 (0개거나 여럿). 아래에서 골라 인자로 주세요:" >&2
    "$GCLOUD" billing accounts list >&2 || true
    exit 2
  fi
  echo "BILLING_ACCOUNT 생략 — 유일한 open 결제 계정을 씁니다: ${BILLING_ACCOUNT}"
fi

STATE_BUCKET="${PROJECT_ID}-tfstate"

echo "=== STEP 1 === 실행 계획 확인"
cat <<EOF
  계정           : ${ACTIVE_ACCOUNT} (격리 구성: ${CLOUDSDK_CONFIG})
  프로젝트 ID    : ${PROJECT_ID}
  결제 계정      : ${BILLING_ACCOUNT}
  리전           : ${REGION}
  tfstate 버킷   : gs://${STATE_BUCKET}
  활성화할 API   : ${REQUIRED_APIS[*]}

이 작업은 과금이 발생하는 리소스를 만들 수 있습니다.
EOF
read -r -p "위 내용으로 진행할까요? (yes 입력): " answer
if [[ "$answer" != "yes" ]]; then
  echo "중단했습니다." >&2
  exit 1
fi

echo "=== STEP 2 === 프로젝트 생성"
# describe는 존재 확인용 프로브다 — 실패가 곧 "없음"이라 출력만 버리고 분기한다.
if "$GCLOUD" projects describe "$PROJECT_ID" >/dev/null 2>&1; then
  echo "프로젝트 ${PROJECT_ID}가 이미 있어 생성을 건너뜁니다."
else
  "$GCLOUD" projects create "$PROJECT_ID"
fi

echo "=== STEP 3 === 결제 계정 연결"
"$GCLOUD" billing projects link "$PROJECT_ID" --billing-account="$BILLING_ACCOUNT"

echo "=== STEP 4 === API 활성화"
"$GCLOUD" services enable "${REQUIRED_APIS[@]}" --project="$PROJECT_ID"

echo "=== STEP 5 === tfstate 버킷 생성"
# 버킷 이름은 전역 유일이라, 남의 버킷을 잘못 잡지 않도록 프로젝트를 명시해 확인한다.
if "$GCLOUD" storage buckets describe "gs://${STATE_BUCKET}" --project="$PROJECT_ID" >/dev/null 2>&1; then
  echo "버킷 gs://${STATE_BUCKET}가 이미 있어 생성을 건너뜁니다."
else
  "$GCLOUD" storage buckets create "gs://${STATE_BUCKET}" \
    --project="$PROJECT_ID" \
    --location="$REGION" \
    --uniform-bucket-level-access \
    --public-access-prevention
fi

# state 손상 시 되돌릴 수 있는 유일한 수단이라 versioning은 필수다.
"$GCLOUD" storage buckets update "gs://${STATE_BUCKET}" --versioning

echo "=== STEP 6 === 완료"
cat <<EOF
부트스트랩이 끝났습니다. 다음 순서로 진행하세요 (자세한 순서는 gcp/README.md).

  1) infra/base/terraform.tfvars.example을 terraform.tfvars로 복사해 값 채우기
  2) make base-init PROJECT_ID=${PROJECT_ID}
     make base-plan PROJECT_ID=${PROJECT_ID}
     make base-apply PROJECT_ID=${PROJECT_ID}
  3) 프록시 토큰 주입 (값은 어떤 파일에도 남기지 않는다)
     printf '%s' "<토큰>" | gcloud secrets versions add pm-backend-token --data-file=- --project=${PROJECT_ID}
  4) HF 토큰 시크릿(hf-token) 준비 후 모델 직행 적재: gcp/fetch_models_remote.sh --all <models_dir>
  5) 최초 이미지 빌드: gcp/deploy.sh --initial
  6) make -C infra run-init run-plan run-apply   (tfvars 불필요 — 기본값으로 생성)
  7) 이후 롤아웃은 gcp/deploy.sh
EOF
