# PIEmgmaker 아키텍처

## 1. 개요

PIEmgmaker는 배너에 삽입할 그래픽 오브젝트를 알파 배경 PNG로 산출한다. 배너를 통으로 합성하지 않고 오브젝트 머테리얼만 생성하며, 캠페인 텍스트는 컨셉 참고용으로만 쓰이고 렌더링되지 않는다.

3원칙:

1. 브랜드 자산은 생성하지 않고 합성한다 (로고 변형 포함, 자산 라이브러리에서 삽입만).
2. 배경 제거는 파이프라인의 책임이다.
3. 알파는 이진 마스크가 아니라 연속값(soft alpha)이다 — 유리·반투명 재질을 지원한다.

시스템은 결정론 구간과 생성 구간으로 나뉜다. 결정론 구간(스키마 검증·후처리·paste-back·코어 해시 검증·QA·지표)은 동일 입력에 동일 출력을 보장하고 단위 테스트로 검증한다. 생성 구간(ComfyUI 실행)은 골든 세트로 검증한다. 단위 테스트는 ComfyUI HTTP를 전부 모킹한다.

## 2. 리포·패키지 구성

Python 패키지(uv, Python 3.12) + FastAPI 서버 + Next.js 웹의 3부 구성이다.

```
src/piemgmaker/
├── config.py            # 환경 설정 3키 로더
├── schemas/             # brief · generation · style_pack · asset · qa · manifest · model_profile
├── engine/              # contract · local_comfy · remote_comfy · hosted_api
├── workflows/           # object_gen_* · object_inpaint_* · render.py + fragments/(matting_segment · matting_trimap)
├── pipeline/            # orchestrate · matting_router · postprocess · paste_back · qa · _morph
├── assets_lib/          # library · registry
├── golden/runner.py     # 골든 세트 러너
├── server/              # app(FastAPI 라우트) · jobs(파일 기반 JobStore + 단일 워커 큐) · history(스캔·검색)
├── bench.py             # 배경 제거 비교 하니스
├── matting_runners.py   # 배경 제거 전략 러너 (bench가 사용)
└── cli.py               # piemgmaker run | manifest | golden | bench | serve

web/                     # Next.js (App Router, TypeScript)
├── app/                 # gate · page(생성) · history · history/[id] · assets · api/pm · api/gate
├── components/          # BackgroundToggle · BriefForm · CandidateCard · JobSettings · QueuePanel · SampleGallery · Sidebar · SidebarStatus · StatusProgress
└── lib/                 # api · gate · progress
```

## 3. 설정 계약

환경 설정은 기본적으로 3키로만 분기한다. 예외는 선택적 온디맨드 기동 레이어로, `PM_COMFY_ONDEMAND_DIR`와 기동 스크립트(`comfy_up.sh`) 존재 여부를 추가로 감지한다.

- `PM_BACKEND_URL` — 기동 중인 ComfyUI 서버 주소 (기본 `http://127.0.0.1:8188`)
- `PM_BACKEND_AUTH` — 백엔드 인증 토큰 (기본 빈 값; 원격 ComfyUI가 인증을 요구할 때만 설정)
- `PM_STORAGE` — 산출물 루트 디렉토리 (기본 `./out`)

설정 예시는 [.env.example](../.env.example) 참조.

## 4. 엔진 계약

`WorkflowEngine` Protocol을 따른다.

```python
class WorkflowEngine(Protocol):
    name: str
    def submit(self, payload: JobPayload) -> JobHandle: ...
    def poll(self, handle: JobHandle) -> JobStatus: ...   # QUEUED | PREPARING | RUNNING(n/k) | DONE | FAILED
    def fetch(self, handle: JobHandle, dest_dir: Path) -> list[CandidateResult]: ...
```

- `JobPayload`는 렌더 완료된 워크플로우 그래프와 버전 핀(워크플로우 id·해시, model_manifest, 스타일 팩 id·version, 매팅 전략·모델 해시, 시드, 사이즈)을 담는다. 히스토리의 "이 설정으로 다시 생성"이 이 페이로드를 그대로 재사용한다.
- `JobPayload.output_index`는 다중 출력 워크플로우에서 채택할 레이어를 지정한다(`-1`은 마지막 레이어).
- 구현체: `LocalComfy`는 ComfyUI의 `/prompt`·`/history` API로 submit/poll/fetch하고 타임아웃 시 FAILED로 처리한다. `RemoteComfy`는 스텁이다. `HostedAPI`는 스텁이며, protected_asset을 포함한 페이로드를 거부하는 가드를 코드로 명시한다.

## 5. 파이프라인 & 데이터 흐름

```
BriefInput(yaml/json) ─ validate ─→ 스타일 팩·자산 resolve ─→ GenerationParams(버전 핀)
   ├─ 자산 없음        → object-gen
   │     opaque:      단색 배경 강제 txt2img + segment
   │     translucent: native_alpha (폴백 trimap)
   ├─ 자산 있음        → object-inpaint
   │     자산 선배치 → 2단 마스크(코어 + 전이 밴드) 인페인팅 → 위 전략 동일
   └─ reference_images → object-gen-styleref (Qwen-Image-Edit-2511)
         프롬프트에 "in the style of the reference image" 자동 부가, 배경 제거는 opaque segment
   ▼ engine.submit / poll / fetch   (후보 k장, 카드별 시드 기록)
   ▼ postprocess (결정론)
   ▼ [자산 경로] paste_back → verify_core_hash (불일치 → 재시도 1회 → 실패)
   ▼ run_qa (하드 룰, material 인지)
```

## 6. 배경 제거 라우팅 & 채택 트랙

`MattingRouter.chain_for(material)`가 재질에 따라 전략 폴백 체인을 반환한다.

- `opaque` → `[segment]`
- `translucent` → `[native_alpha, trimap]`

폴백 체인이 소진되면 잡 실패(재생성 힌트 포함)로 처리한다. 전략 결과는 `AlphaResult(rgba, strategy_id, confidence)`로 통일한다.

채택 트랙:

- opaque → **segment** (BiRefNet-matting + refine_foreground) — 불투명 오브젝트 품질에 충분하고 체인이 단순하다.
- translucent → **native_alpha**(Qwen-Image-Layered) 1순위, **trimap**(ViTMatte) 폴백 — 네이티브 알파가 soft alpha 충실도가 가장 높고, trimap 폴백도 색 오염이 거의 없다.

배선 제약:

- BiRefNet 노드는 optional 입력(mask_blur 등)을 필수 참조하므로 조각에 기본값을 명시한다.
- 매팅 가중치는 첫 실행 시 자동 다운로드되므로 배포 이미지에는 사전 포함하고 model_manifest에 핀한다.
- Qwen-Image-Layered 출력은 `[컴포지트, 레이어…]` 순서라 마지막 장이 투명 오브젝트 레이어다. `JobPayload.output_index=-1`로 배선한다.
- native_alpha는 후처리 매팅 없이 생성 단계에서 알파가 그대로 나오는 트랙으로, 전용 모델·전용 VAE를 쓰며 기본 모델과 분리된다.

## 7. 결정론 모듈

```python
def postprocess(rgba: Image, spec: PostprocessSpec) -> Image
    # 디프린지 → bbox 크롭 → 여백 정규화. 동일 입력 → 동일 출력.

def paste_back(gen: Image, asset: AssetVariant, placement: Placement) -> Image
def verify_core_hash(result: Image, asset: AssetVariant, placement: Placement) -> bool
    # 코어 영역 픽셀 buffer의 sha256 정확 일치 (지각 해시 아님). 불일치 → 재시도 1회 → 실패.

def run_qa(rgba: Image, material: MaterialClass) -> QAReport
    # 경계 접촉 검사 / 알파 커버리지 하한·상한 / 헤일로 검사(material 인지로 soft alpha 오탐 방지)
```

## 8. 모델 프로파일

[model_profiles.yaml](../model_profiles.yaml)이 워크플로우·샘플링·sha256 핀의 단일 소스다. 스타일 팩은 스타일만 소유한다.

- `default`: qwen-image.
- 프로파일 2종:
  - **qwen-image** — workflow_gen `object-gen-qwen-v1`, workflow_inpaint `object-inpaint-qwen-v1`, workflow_styleref `object-gen-styleref-v1`, supports_negative `true`.
  - **flux2-dev** — workflow_gen `object-gen-flux2-v1`, workflow_inpaint `object-inpaint-flux2-v1`, styleref 미지원(참조 이미지는 qwen-image), supports_negative `false`.
- qwen-image에는 native_alpha 서브프로파일(workflow `object-gen-native-alpha-v1`, Qwen-Image-Layered + 전용 VAE)이 있다.
- 각 프로파일 manifest는 unet/clip/vae 파일을 sha256로 핀한다. qwen 계열 3워크플로우는 clip을 공유하고, flux2-dev의 텍스트 인코더는 fp8을 쓴다.
- 라이선스: Qwen-Image·Qwen-Image-Layered는 Apache 2.0. FLUX.2 dev는 Non-Commercial 계열이라 사용 시 라이선스 확인이 사용자 책임이다.

## 9. 스타일 팩 & 자산 라이브러리

- **스타일 팩**: `style_packs/`에 12종 YAML(기본 제공 — 교체·커스터마이즈 가능). 팩은 스타일만 소유하고 모델 가중치는 소유하지 않는다.
- **자산 라이브러리**: 파일 기반이다. `assets/manifest.yaml` + `assets/<asset-id>/<variant-id>.png` 구조이며, 매 요청 로드라 등록 즉시 반영된다.
- **등록 검증**: RGBA + 알파 채널 존재, 완전 불투명 코어 존재(코어 해시 검증 전제), id kebab 규칙·중복 검사, 파일 크기 상한. PNG만 지원한다.
- **저장**: 원자적 재작성(tmp → rename)으로 이루어지며, 서버 내 `threading.Lock`으로 워커의 빌드 로드와 경합을 막는다. 삭제는 숨김(soft delete)이다.

## 10. 웹 아키텍처

FastAPI + Next.js 2계층이다. 잡 실행·상태·`/api/manifest`는 FastAPI가, 게이트·화면·프록시는 Next.js가 담당한다. 잡은 `PM_STORAGE/jobs/<id>/` 파일 기반으로 저장하며 별도 DB가 없다(히스토리는 디렉토리 스캔).

- 페이지: gate(입장) → `page.tsx`(생성하기) → `history/[id]`(리뷰·확정) → `history`(히스토리) → `assets`(자산 라이브러리). Next.js는 `api/pm/[...path]`로 FastAPI에 프록시한다.
- 게이트: `GATE_PASSWORD` 파생 키로 서명한 쿠키 12h.
- 진행 상태: `status.json`에 `queued | preparing | running(n/k) | postprocess | done | failed | canceled`를 기록하고 리뷰 화면이 2초 폴링한다. 재생성은 두 가지 — 같은 설정에 새 시드는 rerun, 입력 수정 후 재생성은 생성 폼 프리필이다.

API 표면(FastAPI, `/api` 접두사):

```
POST /api/jobs                          → {job_id}
POST /api/jobs/{id}/rerun               → {job_id}   # 저장된 payload.json 버전 핀 재사용
GET  /api/jobs/{id}                     → 상태·입력·후보(시드·QA)·확정
GET  /api/jobs?q=&pack=&page=           → 히스토리 목록
GET  /api/queue                         → 큐 스냅샷
POST /api/jobs/{id}/cancel              → 취소
GET  /api/jobs/{id}/images/{kind}/{name} → PNG (kind: candidates|final|inputs)
GET  /api/jobs/{id}/export?indices=     → ZIP (stdlib zipfile)
POST /api/jobs/{id}/select              → 확정 기록
GET  /api/style-packs                   → 팩 목록 · 사이즈 프리셋
GET  /api/models                        → 기본 모델 · 모델 목록
GET  /api/assets                        → 자산 목록
POST /api/assets                        → 자산 등록 (multipart)
POST /api/assets/{id}/variants          → variant 추가 (multipart)
POST /api/assets/{id}/archive           → 자산/variant 숨김·복원
GET  /api/assets/{id}/preview/{variant}.png → 자산 미리보기 PNG
POST /api/uploads                       → 참조 이미지 업로드
GET  /api/uploads/{name}                → 참조 이미지 서빙
GET  /api/samples                       → 샘플 갤러리 매니페스트
GET  /api/samples/{name}                → 샘플 PNG
GET  /api/manifest                      → 자가 보고
```

## 11. 골든 세트 & 벤치 하니스

- **골든 세트**: `golden/cases/*.yaml`(id, expected, thresholds `ssim_min`·`iou_min`·`mae_max`). 임계값은 config가 아니라 케이스 파일에 명시한다. 지표는 RGB SSIM(중립 회색 합성 후) + 알파 이진화 IoU + soft alpha MAE. 실행은 `piemgmaker golden`이다. 형식은 [golden/README.md](../golden/README.md) 참조.
- **벤치 하니스**: `bench/matting_set/set.yaml`(items: id·rgb·gt_alpha?·material). `gt_alpha`가 있는 항목만 MAE·boundary MAE를 계산한다. 실행은 `piemgmaker bench`다. 형식은 [bench/matting_set/README.md](../bench/matting_set/README.md) 참조.

## 12. 산출물 레이아웃

```
PM_STORAGE/jobs/<job_id>/
├── payload.json               # 재현용 잡 페이로드 (버전 핀)
├── candidates/<i>_<seed>.png  # 알파 PNG
├── qa_report.json             # 후보별 룰 판정 + 재생성 힌트
├── status.json                # 상태 전이 기록
└── brief.yaml                 # 입력 브리프
```

## 운영 노트

- 서로 다른 대형 모델 계열(Qwen 스택·FLUX.2 스택)을 한 ComfyUI 프로세스에 공존시키면 메모리 초과 위험이 있다. 모델 계열을 전환할 때는 엔진 재기동을 권장한다.
- FLUX.2 텍스트 인코더는 fp8을 쓴다. bf16 대비 메모리 사용량이 줄어든다.
- CLI 잡 실행 타임아웃 기본값은 명령마다 다르다. `run`은 600초(`--timeout`), `serve` 워커는 14400초(4시간)로, 저속 환경을 고려한 값이다.
