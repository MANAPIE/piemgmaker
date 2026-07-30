# PIEmgmaker ComfyUI 이미지 — Cloud Run GPU 두 서비스(pm-comfy-qwen / pm-comfy-flux2) 공용.
#
# CUDA 12.8 고정: 두 서비스가 이 이미지를 공유하는데 Cloud Run의 사전 설치 드라이버가 GPU마다
# 다르다(L4 535.x / RTX PRO 6000 Blackwell 580.x). CUDA 13은 드라이버 580 이상을 요구해 L4에서
# 뜨지 않고, 12.8은 sm_120(Blackwell) 커널을 내보내는 최소 툴킷이면서 CUDA 12.x 마이너 버전
# 호환으로 535.x에서도 그대로 돈다. 올릴 때 이 두 조건을 함께 만족하는지 확인한다.
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04

LABEL org.opencontainers.image.title="pm-comfy" \
      org.opencontainers.image.description="PIEmgmaker ComfyUI backend for Cloud Run GPU"

# ── 버전 핀 ───────────────────────────────────────────────────────────────────
# 리포에 ComfyUI 본체·커스텀 노드 버전 핀이 없던 문제를 여기서 해소한다.
# 커스텀 노드 3계열은 릴리스 태그가 없어 커밋 해시로 핀한다.
ARG COMFYUI_COMMIT=34d0629452cac83dc20aa3d84e45c9b60d9e36b3
ARG COMFYUI_GGUF_COMMIT=6ea2651e7df66d7585f6ffee804b20e92fb38b8a
ARG COMFYUI_RMBG_COMMIT=615c1146d649bf7d720817a540e0b358466f0558
ARG COMFYUI_IMAGE_MATTING_COMMIT=c65d190dfbdec2a67f81401615a2cdef43665649

# torch는 cu128 인덱스에서 받는다 — PyPI 기본 휠은 베이스와 CUDA 세대가 어긋날 수 있다.
ARG TORCH_VERSION=2.9.1
ARG TORCHVISION_VERSION=0.24.1
ARG TORCHAUDIO_VERSION=2.9.1
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cu128

# transformers는 4.x로 핀한다 — 5.x가 huggingface-hub>=1.5를 요구하는데,
# ComfyUI-RMBG가 huggingface-hub 1.0에서 제거된 local_dir_use_symlinks 인자를 아직 넘긴다.
ARG TRANSFORMERS_VERSION=4.57.1
ARG HUGGINGFACE_HUB_VERSION=0.36.2
ARG GGUF_VERSION=0.19.0
ARG PROTOBUF_VERSION=5.29.6
ARG OPENCV_VERSION=4.13.0.92

# timm은 ComfyUI-RMBG의 requirements.txt에 없는 미선언 의존성이다 — 아래에서 굽는
# BiRefNet 모델 코드가 timm.models.layers/registry(deprecated 셰임)를 직접 import한다.
# timm을 올릴 때는 그 두 경로가 남아 있는지 먼저 확인한다.
ARG TIMM_VERSION=1.0.26

ENV COMFY_HOME=/opt/ComfyUI \
    PM_HOME=/opt/pm \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:/usr/local/nvidia/bin:/usr/local/cuda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive

# libglib2.0-0t64 — Ubuntu 24.04(noble)의 t64 전환 이름. opencv 런타임 의존.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      gettext-base \
      git \
      libglib2.0-0t64 \
      nginx \
      python3 \
      python3-venv \
 && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv "$VIRTUAL_ENV" \
 && pip install --upgrade pip setuptools wheel

RUN pip install \
      --index-url "$TORCH_INDEX_URL" \
      "torch==${TORCH_VERSION}" \
      "torchvision==${TORCHVISION_VERSION}" \
      "torchaudio==${TORCHAUDIO_VERSION}"

RUN git clone --filter=blob:none https://github.com/comfyanonymous/ComfyUI.git "$COMFY_HOME" \
 && git -C "$COMFY_HOME" checkout --detach "$COMFYUI_COMMIT"

RUN git clone --filter=blob:none https://github.com/city96/ComfyUI-GGUF.git \
      "$COMFY_HOME/custom_nodes/ComfyUI-GGUF" \
 && git -C "$COMFY_HOME/custom_nodes/ComfyUI-GGUF" checkout --detach "$COMFYUI_GGUF_COMMIT" \
 && git clone --filter=blob:none https://github.com/1038lab/ComfyUI-RMBG.git \
      "$COMFY_HOME/custom_nodes/ComfyUI-RMBG" \
 && git -C "$COMFY_HOME/custom_nodes/ComfyUI-RMBG" checkout --detach "$COMFYUI_RMBG_COMMIT" \
 && git clone --filter=blob:none https://github.com/hackkhai/ComfyUI-Image-Matting.git \
      "$COMFY_HOME/custom_nodes/ComfyUI-Image-Matting" \
 && git -C "$COMFY_HOME/custom_nodes/ComfyUI-Image-Matting" checkout --detach "$COMFYUI_IMAGE_MATTING_COMMIT"

# 커스텀 노드 requirements.txt를 그대로 쓰지 않는다:
#   - ComfyUI-RMBG는 SAM2/SAM3/GroundingDINO까지 끌어오지만 이 리포가 쓰는 노드는 BiRefNetRMBG 하나다.
#     RMBG의 __init__.py는 노드 파일별로 import 실패를 잡아 건너뛰므로 미설치 노드는 등록되지 않을 뿐이다.
#   - ComfyUI-Image-Matting은 transformers를 git main으로 지정한다(핀 불가) → 위 ARG 핀으로 대체.
# 한 번에 해결(resolve)하도록 ComfyUI 요구사항과 같은 pip 호출에 넣는다 — 충돌 시 빌드가 즉시 실패한다.
RUN pip install \
      -r "$COMFY_HOME/requirements.txt" \
      "transformers==${TRANSFORMERS_VERSION}" \
      "huggingface-hub==${HUGGINGFACE_HUB_VERSION}" \
      "gguf==${GGUF_VERSION}" \
      "protobuf==${PROTOBUF_VERSION}" \
      "opencv-python-headless==${OPENCV_VERSION}" \
      "timm==${TIMM_VERSION}"

# ── 매팅 가중치 사전 포함 ─────────────────────────────────────────────────────
# 출처가 노드 소스에서 확인된다:
#   BiRefNet-general/-matting : ComfyUI-RMBG py/AILab_BiRefNet.py의 MODEL_CONFIG → HF 리포 1038lab/BiRefNet,
#                               캐시 경로 <models>/RMBG/BiRefNet
#   vitmatte_small            : ComfyUI-Image-Matting nodes.py의 matting_model_list → HF 리포
#                               hustvl/vitmatte-small-composition-1k, 캐시 경로
#                               <models>/matting_models/hustvl/vitmatte-small-composition-1k
# 두 노드 모두 캐시 경로에 파일이 있으면 다운로드를 건너뛴다 → 콜드 스타트에서 네트워크 왕복이 사라진다.
# 이 가중치들은 이미지에 굽는다. /models(GCS FUSE)는 읽기 전용이라 런타임 다운로드 목적지로 쓸 수 없다.
# 줄 이음(\)은 셸 토큰 경계에서만 쓴다 — 따옴표 안에서 이으면 Dockerfile 파서와 셸의
# 처리 순서에 기대게 되어 깨지기 쉽다. 그래서 python -c 인자는 각각 한 줄로 둔다.
RUN set -eu; \
    export HF_HOME=/tmp/hf-build; \
    for f in birefnet.py BiRefNet_config.py config.json BiRefNet-general.safetensors BiRefNet-matting.safetensors; do \
      python -c "import sys; from huggingface_hub import hf_hub_download; hf_hub_download(repo_id='1038lab/BiRefNet', filename=sys.argv[1], local_dir=sys.argv[2])" "$f" "$COMFY_HOME/models/RMBG/BiRefNet"; \
    done; \
    python -c "import sys; from transformers import VitMatteForImageMatting, VitMatteImageProcessor; src = 'hustvl/vitmatte-small-composition-1k'; dest = sys.argv[1]; VitMatteForImageMatting.from_pretrained(src).save_pretrained(dest); VitMatteImageProcessor.from_pretrained(src).save_pretrained(dest)" "$COMFY_HOME/models/matting_models/hustvl/vitmatte-small-composition-1k"; \
    rm -rf /tmp/hf-build

# ── 미선언 의존성 가드 ────────────────────────────────────────────────────────
# 커스텀 노드는 import 실패를 삼키므로 미선언 의존성이 빌드·기동·스모크를 통과하고 첫 생성의
# 모델 로드에서야 터진다. 굽는 모델 코드의 최상위 import를 여기서 정적 검사해 그 지연을 없앤다.
# ViTMatte 쪽은 위 baking 단계가 from_pretrained로 실제 import를 거치므로 별도 검사가 없다.
COPY check_baked_deps.py ${PM_HOME}/check_baked_deps.py
RUN python "$PM_HOME/check_baked_deps.py" "$COMFY_HOME/models/RMBG/BiRefNet/birefnet.py"

COPY extra_model_paths.yaml ${PM_HOME}/extra_model_paths.yaml
COPY nginx.conf.template ${PM_HOME}/nginx.conf.template
COPY entrypoint.sh ${PM_HOME}/entrypoint.sh
RUN chmod 0755 "$PM_HOME/entrypoint.sh"

# 가중치를 모두 구웠으므로 런타임 HF 접근은 없어야 한다.
# 오프라인 강제 — 누락이 있으면 조용한 다운로드 대신 즉시 실패로 드러난다.
ENV HF_HUB_OFFLINE=1 \
    PM_MODELS_DIR=/models \
    PM_EXTRA_MODEL_PATHS="$PM_HOME/extra_model_paths.yaml"

# Cloud Run이 $PORT를 주입한다(기본 8080). 문서용 선언.
EXPOSE 8080

ENTRYPOINT ["/opt/pm/entrypoint.sh"]
