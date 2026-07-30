# PIEmgmaker 인프라 진입점.
#
# backend "gcs" 블록에는 변수를 쓸 수 없다. 그래서 root마다 tfstate 버킷과 prefix를
# init 시점에 -backend-config로 주입한다. prefix는 root별로 분리한다 —
# 같은 prefix를 쓰면 뒤에 apply한 root가 앞선 root의 state를 덮어써 파괴적이다.
#
# apply는 사람이 plan을 검토한 뒤 직접 실행한다. 자동 apply 경로는 두지 않는다.

TF ?= terraform

# 계정 격리 — gcp/env.sh와 같은 전용 gcloud 홈을 강제한다. 전역 gcloud에 어떤 계정이
# 활성이든 terraform(ADC)·gcloud 모두 이 디렉토리의 자격만 쓴다.
# 재정의는 PM_GCLOUD_CONFIG_DIR 하나로만 한다 (gcp/env.sh와 기본값 동일 유지).
PM_GCLOUD_CONFIG_DIR ?= $(HOME)/.config/piemgmaker/gcloud
export CLOUDSDK_CONFIG := $(PM_GCLOUD_CONFIG_DIR)
export GOOGLE_APPLICATION_CREDENTIALS := $(PM_GCLOUD_CONFIG_DIR)/application_default_credentials.json

# PROJECT_ID 생략 시 격리 구성의 기본 프로젝트를 쓴다 (gcp/login.sh <PROJECT_ID>로 설정).
# 미설정이면 빈 값이 되어 CHECK_PROJECT가 막는다.
# 주의: $(shell)은 make의 export를 상속하지 않는다 — CLOUDSDK_CONFIG를 인라인으로
# 강제하지 않으면 전역 gcloud의 프로젝트(다른 계정 것)가 새어 들어온다.
PROJECT_ID ?= $(shell CLOUDSDK_CONFIG=$(PM_GCLOUD_CONFIG_DIR) gcloud config get-value project 2>/dev/null)
STATE_BUCKET ?= $(PROJECT_ID)-tfstate

# 해석된 PROJECT_ID를 terraform 변수로도 넘긴다 — tfvars에 project_id를 적지 않아도 된다.
# 환경변수는 terraform.tfvars보다 우선순위가 낮아, tfvars에 적으면 그 값이 이긴다.
ifneq ($(PROJECT_ID),)
export TF_VAR_project_id := $(PROJECT_ID)
endif

# 전용 ADC가 없으면 자격이 필요한 타깃을 진행하지 않는다 (validate·fmt는 자격 불필요라 제외).
CHECK_AUTH = @test -f "$(GOOGLE_APPLICATION_CREDENTIALS)" || { echo "전용 ADC가 없습니다 — 최초 1회 gcp/login.sh를 실행하세요 (기대 경로: $(GOOGLE_APPLICATION_CREDENTIALS))" >&2; exit 1; }

BASE_PREFIX := piemgmaker/base
RUN_PREFIX := piemgmaker/run

CHECK_PROJECT = @test -n "$(PROJECT_ID)" || { echo "PROJECT_ID가 필요합니다 — 인자로 주거나(make base-plan PROJECT_ID=my-project) gcp/login.sh <PROJECT_ID>로 격리 구성에 기본 프로젝트를 설정하세요" >&2; exit 1; }

.PHONY: help fmt fmt-check validate base-init base-plan base-apply run-init run-plan run-apply

help:
	@echo "사용법: make <타깃> PROJECT_ID=<gcp-project-id>"
	@echo ""
	@echo "  base-init / base-plan / base-apply   버킷·Artifact Registry·SA·시크릿·예산"
	@echo "  run-init  / run-plan  / run-apply    Cloud Run GPU 서비스 2종"
	@echo "  fmt / fmt-check / validate           포맷·문법 검사 (자격 증명 불필요)"

fmt:
	$(TF) fmt -recursive

fmt-check:
	$(TF) fmt -check -recursive

# 백엔드 없이 초기화하므로 GCP 자격 증명 없이도 돌아간다.
validate:
	cd base && $(TF) init -backend=false -input=false && $(TF) validate
	cd run && $(TF) init -backend=false -input=false && $(TF) validate

base-init:
	$(CHECK_PROJECT)
	$(CHECK_AUTH)
	cd base && $(TF) init -reconfigure -input=false \
		-backend-config="bucket=$(STATE_BUCKET)" \
		-backend-config="prefix=$(BASE_PREFIX)"

base-plan:
	$(CHECK_PROJECT)
	$(CHECK_AUTH)
	cd base && $(TF) plan -input=false

base-apply:
	$(CHECK_PROJECT)
	$(CHECK_AUTH)
	@printf 'base root(버킷·레지스트리·SA·시크릿·예산)에 apply합니다. 계속하려면 yes 입력: '; \
	read -r answer; \
	[ "$$answer" = "yes" ] || { echo "중단했습니다." >&2; exit 1; }
	cd base && $(TF) apply

run-init:
	$(CHECK_PROJECT)
	$(CHECK_AUTH)
	cd run && $(TF) init -reconfigure -input=false \
		-backend-config="bucket=$(STATE_BUCKET)" \
		-backend-config="prefix=$(RUN_PREFIX)"

run-plan:
	$(CHECK_PROJECT)
	$(CHECK_AUTH)
	cd run && $(TF) plan -input=false

run-apply:
	$(CHECK_PROJECT)
	$(CHECK_AUTH)
	@printf 'run root(GPU 서비스 2종)에 apply합니다. 계속하려면 yes 입력: '; \
	read -r answer; \
	[ "$$answer" = "yes" ] || { echo "중단했습니다." >&2; exit 1; }
	cd run && $(TF) apply
