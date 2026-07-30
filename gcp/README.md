# gcp/ — Cloud Run ComfyUI 백엔드 컨테이너·배포 스크립트

PIEmgmaker 원격 백엔드의 **컨테이너와 롤아웃**을 담는다.
프로젝트·버킷·서비스·시크릿 컨테이너·예산 알림 같은 **베이스 인프라는 `infra/`(Terraform) 소유**다.
경계는 "베이스 = TF / 배포 = 저장소 스크립트"다.

> **이 디렉토리의 스크립트는 전부 사용자가 직접 실행한다.**
> 자동 실행·CI 연결 대상이 아니다. `deploy.sh`·`sync_models.sh`는 비대화형으로 실행되면
> 확인 프롬프트를 띄울 수 없어 스스로 중단한다.

## 구성

| 파일 | 역할 |
|---|---|
| `env.sh` | **계정 격리 레이어** — 모든 스크립트·Make가 공유하는 전용 gcloud 홈(`CLOUDSDK_CONFIG`) 설정 (source 전용) |
| `login.sh` | 최초 1회 인증 — 전용 디렉토리에 계정 로그인 + ADC 발급 |
| `remote_models.tsv` | 원격 필요 모델 목록의 **단일 소스** (버킷 경로·다운로드 URL·sha256) |
| `fetch_models_remote.sh` | **원격 전용 모델을 GCP 안에서 HF → 버킷으로 직행** (Cloud Build, 로컬 회선 미사용 — 권장) |
| `download_models.sh` | 원격 필요 모델을 로컬로 다운로드 (이어받기 지원 — 로컬 사본이 필요할 때만) |
| `Dockerfile` | CUDA 12.8 + Python 3.12 + ComfyUI·커스텀 노드 3계열(커밋 해시 핀) + 매팅 가중치 사전 포함 + nginx |
| `extra_model_paths.yaml` | ComfyUI 모델 경로 → `/models`(GCS FUSE, 읽기 전용) 매핑 |
| `nginx.conf.template` | 인증 프록시 설정 템플릿. `entrypoint.sh`가 envsubst로 렌더 |
| `entrypoint.sh` | nginx + ComfyUI 기동, 자기 종료 와치독 |
| `deploy.sh` | Cloud Build 빌드 → AR 푸시 → Cloud Run 두 서비스 이미지 갱신 (`--initial`: 최초 빌드 전용) |
| `sync_models.sh` | 로컬 모델 → `gs://<PROJECT_ID>-models` 단방향 업로드 |

## 사전 준비 — 인증 (계정 격리, 최초 1회)

이 저장소의 모든 GCP 도구는 **머신의 전역 gcloud와 격리된 전용 환경**에서 동작한다.
`env.sh`가 `CLOUDSDK_CONFIG`를 전용 디렉토리(기본 `~/.config/piemgmaker/gcloud`)로
돌려서, gcloud 자격 증명·구성·ADC가 통째로 그 안에 격리된다. 전역 gcloud에 어떤
계정·프로젝트가 활성이든 서로 영향을 주고받지 않는다 — 다른 프로젝트에서 gcloud를
쓰고 있어도 아무것도 바꿀 필요가 없다.

```sh
./login.sh              # 브라우저 2회 (계정 로그인 + ADC) — 이 프로젝트에 쓸 계정 선택
./login.sh <PROJECT_ID> # 프로젝트를 이미 만들었으면 기본 프로젝트·quota project까지 설정
```

- terraform(`infra/Makefile`)도 같은 격리 ADC를 쓴다 — Makefile이 env를 자동으로 잡는다.
- **`login.sh <PROJECT_ID>`로 기본 프로젝트를 설정해 두면** 이후 bootstrap·make·deploy·sync
  전부에서 `PROJECT_ID` 인자를 생략할 수 있다. bootstrap의 결제 계정도 open 계정이
  정확히 1개면 자동 감지된다 (해석된 값은 항상 확인 프롬프트에 표시된다).
- **HF 토큰(무료 계정, read 권한)** 을 시크릿으로 넣어 두면 모델 직행 전송이 인증으로 진행된다 —
  익명은 수 GB 후 KiB/s로 스로틀되므로 사실상 필수다:
  `printf '%s' '<hf_토큰>' | gcloud secrets create hf-token --data-file=-`
- 격리 디렉토리 변경: `PM_GCLOUD_CONFIG_DIR` (env.sh와 infra/Makefile 양쪽에 동일 적용).
- 되돌리기: `rm -rf ~/.config/piemgmaker/gcloud` — 전역 gcloud는 처음부터 무관하다.
- 수동으로 gcloud 명령을 칠 일이 있으면 해당 셸에서 `source gcp/env.sh` 후 실행한다.

## 초기 구축 순서

전부 수동이다. 각 단계가 끝난 뒤 다음으로 넘어간다.

1. **`./login.sh <PROJECT_ID>`** — 위 "사전 준비" 참조 (최초 1회). 프로젝트 ID를 함께 주면 이후 인자 생략 가능.
2. **`infra/bootstrap.sh`** — 프로젝트 생성·결제 연결·API 활성화·tfstate 버킷.
   PROJECT_ID·BILLING_ACCOUNT는 생략 시 격리 구성 기본값·단일 open 결제 계정을 쓴다.
3. **`infra/base` terraform apply** — Artifact Registry `pm-comfy`, 모델 버킷 `<PROJECT_ID>-models`,
   런타임 SA `pm-run-sa`, Secret Manager 컨테이너 `pm-backend-token`, 예산 알림.
   ```sh
   cd infra && make base-init base-plan base-apply PROJECT_ID=<PROJECT_ID>
   ```
4. **토큰 시크릿 수동 주입** — 값은 TF·state·이 저장소 어디에도 두지 않는다.
   ```sh
   # printf 파이프로 넘겨야 값이 명령줄·히스토리·디스크에 남지 않는다.
   source gcp/env.sh
   PM_TOKEN="$(openssl rand -base64 48 | tr -d '\n')"
   printf '%s' "$PM_TOKEN" | gcloud secrets versions add pm-backend-token --data-file=-
   printf 'PM_BACKEND_AUTH=%s\n' "$PM_TOKEN" >> .env
   unset PM_TOKEN
   ```
   허용 문자는 `A-Za-z0-9._+/=-`, 길이 16~512자다(`entrypoint.sh`가 검증한다 — base64 출력은 이 안에 든다).
   nginx 설정 문법을 깨는 문자를 막기 위한 제약이다.
   확인: `gcloud secrets versions list pm-backend-token`에 버전이 보이면 성공.
5. **`./fetch_models_remote.sh --all LOCAL_MODELS_DIR`** — 원격 필요 모델 **전부**를
   **GCP 안에서 HF → 버킷으로 직행**시킨다. Cloud Build가 같은 리전에서 다운로드·
   sha256 핀 검증·업로드를 수행하므로 로컬 회선을 전혀 쓰지 않는다 (ingress 무료).
   `--check`로 계획만 볼 수 있다. 목록·URL·sha256의 단일 소스는 `remote_models.tsv`다.
   전 파일의 sha256이 HF LFS etag로 핀돼 있어 로컬 업로드 없이도 무결성이 보장된다.
   빌드 머신은 e2-highcpu-8이다(4GB 머신은 Xet 다운로드 중 OOM — 실측). 무료 티어 밖이지만
   전체 $1~2 수준. `hf-token` 시크릿이 있으면 자동으로 인증 다운로드한다(사전 준비 참조).
6. *(선택)* **`./sync_models.sh LOCAL_MODELS_DIR`** — 로컬 보유본을 올리고 싶을 때만.
   5단계를 `--all`로 돌렸다면 "전부 버킷에 있음"으로 그냥 종료된다. HF에 없는
   로컬 전용 파일을 나중에 추가할 때를 위한 경로다.
7. **`./deploy.sh --initial`** — 최초 이미지 빌드·push (태그 `bootstrap`).
   run 서비스는 실제로 기동하는 이미지가 있어야 생성되므로(startup probe) **run apply보다
   먼저** 실행해야 한다.
8. **`infra/run` terraform apply** — **tfvars 없이 동작한다**: `project_id`는 Makefile이
   격리 구성에서 `TF_VAR`로 주입하고, 이미지는 7단계의 `bootstrap` 태그가 기본값이다.
   바꿀 값이 있을 때만 `run/terraform.tfvars`를 만든다.
   ```sh
   make -C infra run-init run-plan run-apply
   ```
9. **이후 롤아웃은 `./deploy.sh`** — 빌드 → 푸시 → 두 서비스 이미지 갱신.
10. **스모크** — 아래 참조.

## 스모크 점검

repo 루트에서 아래를 그대로 붙여 넣으면 된다 (프로젝트는 격리 구성 기본값, 토큰은 `.env`에서 읽는다).

```sh
source gcp/env.sh
PM_BACKEND_AUTH="$(grep '^PM_BACKEND_AUTH=' .env | head -1 | cut -d= -f2-)"

# 0) Ready 확인 — 두 서비스 모두 True가 될 때까지 기다린다 (첫 배포는 수 분)
gcloud run services list --region asia-southeast1 \
  --format='table(metadata.name,status.conditions[0].status,status.url)'

# 1~2) 인증 게이트(401) + 기동(200) — 서비스별
for svc in pm-comfy-qwen pm-comfy-flux2; do
  url="$(gcloud run services describe "$svc" --region asia-southeast1 --format='value(status.url)')"
  echo "=== $svc — $url"
  printf '무인증 (401 기대): '
  curl -s -o /dev/null -w '%{http_code}\n' --max-time 120 "$url/system_stats"
  printf '토큰   (200 기대, 콜드 스타트라 수 분 가능): '
  curl -s -o /dev/null -w '%{http_code} (%{time_total}s)\n' --max-time 600 \
    -H "Authorization: Bearer $PM_BACKEND_AUTH" "$url/system_stats"
done

# 3) 유휴 자동 종료 — 위가 끝나고 11분쯤 뒤 실행 (와치독 유휴 임계 9분 + 여유).
#    다시 콜드 스타트(수십 초 이상)면 인스턴스가 스스로 내려갔다는 증거다.
#    1초 미만 200이면 아직 떠 있는 것이니 더 기다린다.
url="$(gcloud run services describe pm-comfy-qwen --region asia-southeast1 --format='value(status.url)')"
curl -s -o /dev/null -w '%{http_code} (%{time_total}s)\n' --max-time 600 \
  -H "Authorization: Bearer $PM_BACKEND_AUTH" "$url/system_stats"
```

0단계의 Ready가 10분 넘게 `Unknown`/`False`면 리비전 로그를 본다:

```sh
gcloud run revisions list --service pm-comfy-qwen --region asia-southeast1
gcloud logging read 'resource.type="cloud_run_revision" resource.labels.service_name="pm-comfy-qwen"' \
  --limit 30 --format='value(textPayload)' --freshness 1h
```

## 컨테이너 계약

| 항목 | 값 |
|---|---|
| 수신 포트 | `$PORT`(Cloud Run 주입, 기본 8080) — nginx 인증 프록시 |
| 인증 | `Authorization: Bearer <PM_PROXY_TOKEN>`. 불일치하면 401 |
| 업스트림 | `127.0.0.1:8188` ComfyUI. 외부에 직접 노출되지 않는다 |
| 모델 | `/models` (GCS FUSE, 읽기 전용) |
| 준비 확인 | `GET /pm-healthz` — 인증 없이 `200 ok` 또는 `503 starting` |

### 환경 변수

| 이름 | 기본값 | 설명 |
|---|---|---|
| `PM_PROXY_TOKEN` | (필수) | Secret Manager `pm-backend-token`에서 주입. 비면 기동하지 않는다 |
| `PORT` | `8080` | Cloud Run이 주입 |
| `PM_IDLE_EXIT_SECONDS` | `90` | 유휴 자기 종료 임계. `0`이면 비활성. **배포 값은 540**(`infra/run`의 `idle_exit_seconds`) |
| `PM_MIN_UPTIME_SECONDS` | `120` | 최소 가동 보장 |
| `PM_WATCHDOG_INTERVAL_SECONDS` | `15` | 와치독 점검 주기 |
| `PM_SHUTDOWN_GRACE_SECONDS` | `20` | 정상 종료 대기 후 강제 종료 |
| `PM_MODELS_DIR` | `/models` | FUSE 마운트 경로. 없으면 기동 실패 |
| `PM_STATUS_PORT` | `8189` | 와치독 전용 nginx stub_status. 루프백만 |

### 자기 종료 와치독

15초 주기로 아래를 **모두** 만족하면 컨테이너를 스스로 내린다. 인스턴스가 회수되면서 GPU 과금이 멈춘다.
Cloud Run에는 유휴 유지 시간을 조절하는 설정이 없어 이 와치독이 유일한 손잡이다.

1. 가동 시간이 `PM_MIN_UPTIME_SECONDS` 초과
2. ComfyUI `/queue`의 `queue_running`·`queue_pending`이 모두 비어 있음
3. nginx `stub_status` 기준 처리 중인 프록시 요청 없음
4. 마지막 프록시 요청(액세스 로그 mtime) 후 `PM_IDLE_EXIT_SECONDS` 경과

3번이 따로 있는 이유: 액세스 로그는 요청 **완료** 시점에 쓰인다.
콜드 스타트 중 진행 중인 첫 업로드는 로그에 없어서, 로그만 보면 생성 중인 잡을 죽일 수 있다.

401 응답은 액세스 로그에 남기지 않는다. 인증 실패 트래픽이 유휴 종료를 미뤄
GPU 과금을 늘리는 경로를 막기 위해서다.

nginx나 ComfyUI 중 하나라도 죽으면 나머지도 내리고 컨테이너를 종료한다(좀비 인스턴스 = 순수 과금).

## 버전 핀

리포에 ComfyUI 본체·커스텀 노드 버전 핀이 없던 문제를 이 이미지에서 해소한다.
값은 `Dockerfile`의 `ARG`이고, 빌드 시 `--build-arg`로 덮어쓸 수 있다.

| 대상 | 핀 |
|---|---|
| CUDA 베이스 | `nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04` |
| ComfyUI | `34d0629452cac83dc20aa3d84e45c9b60d9e36b3` (태그 `v0.28.3`) |
| ComfyUI-GGUF | `6ea2651e7df66d7585f6ffee804b20e92fb38b8a` |
| ComfyUI-RMBG | `615c1146d649bf7d720817a540e0b358466f0558` (v3.1.0) |
| ComfyUI-Image-Matting | `c65d190dfbdec2a67f81401615a2cdef43665649` |
| torch / torchvision / torchaudio | `2.9.1` / `0.24.1` / `2.9.1` (cu128 인덱스) |
| transformers / huggingface-hub | `4.57.1` / `0.36.2` |

CUDA 베이스를 올릴 때의 제약은 `Dockerfile` 주석에 있다 — 두 GPU의 드라이버 세대가 달라
양쪽을 동시에 만족하는 툴킷이어야 한다.

### 매팅 가중치

이미지에 사전 포함한다. `/models`는 읽기 전용이라 노드의 자동 다운로드 목적지로 쓸 수 없고,
콜드 스타트마다 HF 왕복이 붙는 것도 피해야 한다. 출처는 노드 소스에서 확인했다.

| 가중치 | 출처 | 이미지 내 경로 |
|---|---|---|
| `BiRefNet-general`·`BiRefNet-matting` | HF `1038lab/BiRefNet` | `/opt/ComfyUI/models/RMBG/BiRefNet` |
| `vitmatte_small` | HF `hustvl/vitmatte-small-composition-1k` | `/opt/ComfyUI/models/matting_models/hustvl/vitmatte-small-composition-1k` |

런타임에는 `HF_HUB_OFFLINE=1`이다. 굽지 못한 가중치가 있으면 조용히 내려받는 대신 즉시 실패한다.

## 모델 적재

잡 시간을 지배하는 것은 **GCS FUSE 모델 읽기**다(qwen 약 6분 20초). 관련 설정은 두 곳에 나뉜다.

- **컨테이너** — ComfyUI를 `--disable-dynamic-vram`으로 띄운다. `--disable-mmap`은 넣지 않는다
  (근거는 `entrypoint.sh` 주석).
- **인프라** — 마운트 옵션·Direct VPC·Rapid Cache는 `infra/run`의 Terraform이 소유한다.
  이 디렉토리에서는 바꿀 수 없다. 버킷은 서비스와 같은 단일 리전(asia-southeast1)이어야 한다.

전체 구성과 성능 수치는 [docs/architecture.md](../docs/architecture.md)의 「원격 백엔드 구성」 참조.

## 알아둘 것

- Cloud Run 컨테이너의 쓰기 가능 파일시스템은 **인메모리**다. ComfyUI의 `input/`·`output/`·`temp/`에
  쌓이는 만큼 인스턴스 메모리를 먹는다. 잡 단위로 인스턴스가 회수되므로 누적되지는 않는다.
- 렌더된 nginx 설정에는 프록시 토큰이 들어간다. `mktemp -d`(0700) 안의 0600 파일로만 두고
  컨테이너 종료 시 지운다. 토큰 값은 로그·에러 메시지 어디에도 출력하지 않는다.
- 커스텀 노드는 import 실패를 삼킨다 — 미선언 의존성이 빌드·기동·스모크를 통과하고 첫 생성의
  모델 로드에서야 터진다. `check_baked_deps.py`가 빌드 시점에 이를 막는다.
