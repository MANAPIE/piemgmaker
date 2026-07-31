# PIEmgmaker

배너용 **오브젝트 머테리얼 생성기**. 배너를 통으로 만들지 않고, 배너에 들어갈 그래픽 오브젝트를 **알파 배경 PNG**로 산출한다. 캠페인 텍스트는 컨셉 참고용일 뿐 렌더링되지 않는다.

## 원칙

1. 브랜드 자산은 생성하지 않고 **합성**한다 (로고 변형 포함 — 자산 라이브러리에서 삽입만).
2. 배경 제거는 파이프라인의 책임이다.
3. 알파는 이진 마스크가 아니라 연속값(soft alpha)이다 — 유리·반투명 재질을 지원한다.

## 요구 사항

- Python 3.12, uv
- `PM_BACKEND_URL`로 접근 가능한 기동 중인 ComfyUI
- 배경 제거용 커스텀 노드 (BiRefNet-matting · ViTMatte)
- `model_profiles.yaml`에 sha256로 핀된 모델 가중치
- 웹 인터페이스 사용 시 Node.js

## 설치

```bash
uv sync
cd web && npm install
```

## 사용 (CLI)

```bash
uv run piemgmaker manifest                                            # 자가 보고 JSON 출력
uv run piemgmaker run --brief brief.yaml                             # 브리프 실행 → 알파 PNG k장 + qa_report.json
uv run piemgmaker golden --cases golden/cases --produced <디렉토리>    # 골든 세트 러너
uv run piemgmaker bench --set bench/matting_set/set.yaml --dry-run    # 배경 제거 비교 하니스
```

`run`은 후보 알파 PNG k장과 `qa_report.json`을 산출한다. 골든 세트는 초기에 비어 있으며, 케이스는 `golden/cases/`에 등록한다.

## 설정 (환경 변수)

루트 3키 — 환경 분기는 기본적으로 이 값들로만 발생한다. 예외는 아래의 선택적 온디맨드 기동 레이어(`PM_COMFY_ONDEMAND_DIR`·스크립트 존재 감지)다.

- `PM_BACKEND_URL` — 기동 중인 ComfyUI 주소 (기본 `http://127.0.0.1:8188`)
- `PM_BACKEND_AUTH` — 백엔드 인증 토큰 (기본 빈 값)
- `PM_STORAGE` — 산출물 루트 디렉토리 (기본 `./out`)

웹 3키 — `GATE_PASSWORD`, `PM_API_URL`, `NEXT_PUBLIC_BRAND_COLOR`.

원격 백엔드(GCP Cloud Run) 사용 시 추가 키:

- `PM_ENGINE` — `local-comfy`(기본) | `remote-comfy`
- `PM_REMOTE_URL_QWEN` / `PM_REMOTE_URL_FLUX2` — 모델 계열별 원격 서비스 URL (https 필수, `PM_BACKEND_AUTH`가 Bearer 토큰)

예시는 [.env.example](.env.example) · [web/.env.example](web/.env.example) 참조.

리포 루트의 `.env`는 CLI가 자동으로 읽는다. **이미 설정된 실제 환경변수가 `.env`보다 우선**하므로 일회성 우회는 앞에 붙여 쓴다 (`PM_ENGINE=local-comfy uv run piemgmaker serve`). `uvicorn`으로 서버 모듈을 직접 띄우면 `.env`가 적용되지 않으니 `piemgmaker serve`를 쓴다. `run`·`serve`는 시작 시 사용 중인 백엔드를 stderr에 한 줄 출력한다.

엔진은 기본적으로 `PM_BACKEND_URL`의 기동 중인 ComfyUI에 접속한다. 온디맨드 기동 레이어(`comfy_up.sh`)가 감지되면 선택적으로 사용하며, 위치는 `PM_COMFY_ONDEMAND_DIR`로 지정한다. `PM_ENGINE=remote-comfy`면 GCP Cloud Run의 원격 ComfyUI로 라우팅한다 — 구축·운영은 [gcp/README.md](gcp/README.md) 참조.

## 웹 인터페이스

```bash
uv run piemgmaker serve --port 8787            # ① 잡 API (FastAPI, 단일 워커 큐)
cd web && npm run build && npm start           # ② 프론트 (Next.js) → http://localhost:3000
```

- 입장 암호는 `GATE_PASSWORD`(기본 0000), 세션 12h. 다른 기기에서 접속하려면 Next.js만 `next start -H 0.0.0.0`으로 연다(Next가 서버사이드로 FastAPI에 프록시). `piemgmaker serve`는 무인증 서버이므로 `127.0.0.1` 유지를 권장하며, 직접 노출은 신뢰된 네트워크에서만 한다.
- 페이지: 게이트 → 생성하기 → 리뷰·확정 → 히스토리 → **자산 라이브러리** (직접 등록 · 숨김/복원).

## 모델 선택 · 스타일 참조

- **모델**: 생성 화면에서 `qwen-image`(기본)와 `flux2-dev`를 선택한다. 정의는 [model_profiles.yaml](model_profiles.yaml)이 단일 소스(워크플로우·샘플링·sha256 핀)이며, 스타일 팩은 스타일만 소유한다.
- **스타일 참조 이미지**: 최대 2장. Qwen-Image-Edit-2511이 무드·질감을 반영한다. `flux2-dev` 프로파일은 참조 이미지를 지원하지 않으며, 자산 인페인트와 동시 사용은 불가하다.
- **자산 등록**: 알파 PNG만 받고 완전 불투명 코어를 요구한다(코어 해시 검증 전제). 삭제는 숨김(soft delete)이다.

## 디렉토리 구조

```
src/piemgmaker/   # 패키지 (config · schemas · engine · workflows · pipeline · assets_lib · golden · server · bench · matting_runners · cli)
web/              # 웹 인터페이스 (Next.js)
style_packs/      # 스타일 팩 12종 YAML (기본 제공 — 교체·커스터마이즈 가능)
assets/           # 자산 라이브러리 (파일 기반, manifest.yaml)
golden/           # 골든 세트
bench/            # 배경 제거 비교 테스트 세트
samples/          # 웹 샘플 갤러리용 예시 PNG + manifest.json
tests/            # 결정론 구간 단위 테스트
docs/             # 문서
out/              # 산출물 기본 루트
```

## 문서

- 아키텍처와 전체 파이프라인: [docs/architecture.md](docs/architecture.md)
- 원격 백엔드(GCP): [gcp/README.md](gcp/README.md) (컨테이너·배포·모델 적재) · [infra/README.md](infra/README.md) (Terraform)
