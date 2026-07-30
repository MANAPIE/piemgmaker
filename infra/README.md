# infra — PIEmgmaker GCP 인프라

Cloud Run 서버리스 GPU 2서비스(scale-to-zero) 구조의 Terraform 코드다.
전체 구성은 [docs/architecture.md](../docs/architecture.md)의 「원격 백엔드 구성」 참조.

## 구성

| 경로 | state prefix | 소유 리소스 |
|---|---|---|
| `bootstrap.sh` | — | 프로젝트 생성·결제 연결·API 활성화·tfstate 버킷 (최초 1회) |
| `base/` | `piemgmaker/base` | 모델 버킷, Artifact Registry, 런타임 SA·IAM, 시크릿 컨테이너, 예산 알림, Direct VPC용 네트워크·서브넷 |
| `run/` | `piemgmaker/run` | Cloud Run v2 서비스 `pm-comfy-qwen`·`pm-comfy-flux2` (FUSE 마운트 옵션·Direct VPC 연결 포함) |

root를 나눈 이유는 수명 주기가 다르기 때문이다. `base`는 거의 바뀌지 않고 지워지면 곤란한
자산(모델 100GB+)을 들고 있고, `run`은 서비스 사양을 바꿔 가며 자주 갱신한다.
**두 root의 state prefix는 반드시 달라야 한다** — 같으면 나중 apply가 앞선 state를 덮어써 파괴적이다.

## state 백엔드 주입

`backend "gcs"` 블록 안에서는 변수·보간을 쓸 수 없다. tfstate 버킷 이름은 프로젝트 ID에서
파생되므로(`<project_id>-tfstate`) HCL에 하드코딩할 수 없고, 하드코딩하면 프로젝트 ID가
저장소에 박힌다. 그래서 두 root의 `versions.tf`는 `backend "gcs" {}`로 비워 두고
`Makefile`이 init 시 부분 설정을 주입한다.

```
terraform init -reconfigure \
  -backend-config="bucket=<project_id>-tfstate" \
  -backend-config="prefix=piemgmaker/base"
```

문법·포맷 검사는 백엔드 없이 돌아가므로 GCP 자격 증명 없이도 가능하다.

```
make validate     # 두 root init -backend=false + validate
make fmt-check    # terraform fmt -check -recursive
```

## 인증 — 계정 격리

이 Makefile과 `bootstrap.sh`는 머신의 전역 gcloud가 아니라 **전용 gcloud 홈**
(`CLOUDSDK_CONFIG`, 기본 `~/.config/piemgmaker/gcloud`)으로만 동작한다.
terraform도 같은 격리 ADC(`GOOGLE_APPLICATION_CREDENTIALS`)를 쓰도록 Makefile이
env를 강제한다. 전역 gcloud에 다른 계정·프로젝트가 활성이어도 서로 무관하다.

- 최초 1회: `gcp/login.sh <PROJECT_ID>` (계정 로그인 + ADC 발급 + 기본 프로젝트 설정). 없으면 자격이 필요한 타깃이 안내와 함께 중단된다.
- **PROJECT_ID 인자는 생략 가능** — 격리 구성의 기본 프로젝트를 쓴다. `bootstrap.sh`의 결제 계정도 open 계정이 정확히 1개면 자동 감지한다.
- Makefile은 해석된 PROJECT_ID를 **`TF_VAR_project_id`로도 주입**한다 — tfvars에 `project_id`를 적을 필요가 없다 (적으면 tfvars가 이긴다).
- 격리 디렉토리 재정의: `PM_GCLOUD_CONFIG_DIR` (gcp/env.sh와 기본값을 같게 유지할 것).
- `validate`·`fmt`는 자격 증명 없이 돌아간다.

## 실행 순서

전체 순서의 단일 소스는 `gcp/README.md`다. infra 소관 단계만 요약하면
(기본 프로젝트를 설정했다면 `PROJECT_ID=` 인자는 전부 생략 가능):

```
../gcp/login.sh <PROJECT_ID>                             # 최초 1회 인증 + 기본 프로젝트
./bootstrap.sh                                           # 프로젝트·결제·API·tfstate 버킷

cp base/terraform.tfvars.example base/terraform.tfvars   # billing_account·alert_email 등
make base-init base-plan base-apply

# 프록시 토큰 주입 — 값은 어떤 파일에도 쓰지 않는다 (source ../gcp/env.sh 한 셸에서)
printf '%s' "<토큰>" | gcloud secrets versions add pm-backend-token --data-file=-

# 모델 직행 적재(gcp/fetch_models_remote.sh --all)와
# 최초 이미지 빌드(gcp/deploy.sh --initial, bootstrap 태그) 후 —
# run root는 tfvars 없이 생성된다: project_id는 TF_VAR 주입, 이미지는 bootstrap 태그 기본값.
make run-init run-plan run-apply
```

`apply`는 항상 사람이 plan을 검토한 뒤 직접 실행한다. 자동 apply 경로는 두지 않는다.

## 소유권 경계

베이스 인프라는 Terraform이, 그 위의 배포는 저장소 스크립트가 소유한다.

| 대상 | 소유자 |
|---|---|
| 버킷·레지스트리·SA·시크릿 컨테이너·서비스 형상 | Terraform |
| 컨테이너 이미지 빌드·push·서비스 이미지 갱신 | `gcp/deploy.sh` |
| 모델 적재 (HF→버킷 직행이 기본) | `gcp/fetch_models_remote.sh` (`sync_models.sh`는 로컬 전용 파일용 보조) |
| 빌드 SA 권한 4종 (소스 읽기·로그·모델 버킷 쓰기·AR push) | Terraform (`base/storage.tf` — 새 프로젝트의 기본 SA는 롤이 없다) |
| 시크릿 **값** | 사람 (`gcloud secrets versions add`) |
| 모델 버킷의 Rapid Cache (asia-southeast1-a/b/c, TTL 24h) | 사람 — **Terraform 밖이다.** 프로젝트를 새로 만들면 아래 명령을 직접 실행해야 한다 |

```
gcloud storage buckets anywhere-caches create gs://<project_id>-models \
  asia-southeast1-a asia-southeast1-b asia-southeast1-c --admission-policy=ADMIT_ON_FIRST_MISS
```

Cloud Run 서비스의 `image` 필드는 `lifecycle.ignore_changes`에 넣어 TF가 무시한다.
두 주체가 같은 필드를 소유하면 배포할 때마다 서로 되돌리는 diff가 생긴다.

## 주의

- 시크릿 값과 실제 프로젝트 ID는 어떤 파일에도 커밋하지 않는다. `terraform.tfvars`는 `.gitignore` 대상이고 `*.example`만 커밋한다.
- 모델 버킷은 `prevent_destroy` + `force_destroy=false`다. 정말 지워야 하면 lifecycle 블록을 손으로 풀고 나서 destroy한다.
- 모델 버킷은 soft delete 보관을 0초로 둔다. 기본값 7일은 교체·삭제한 대용량 모델을 보관 기간 동안 이중 과금한다.
- GPU 쿼터는 zonal redundancy를 끈 상태에서 첫 배포 시 자동 부여된다(L4 3 GPU, RTX PRO 6000 3,000 milliGPU). `gpu_zonal_redundancy_disabled = true`를 되돌리면 별도 쿼터 신청이 필요하다.
