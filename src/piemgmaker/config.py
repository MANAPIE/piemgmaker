"""config 로더 — 환경 분기는 이 키 값으로만 발생한다.

기본 3키(백엔드 URL·인증·스토리지)에 엔진 선택 키가 더해진다. PM_ENGINE 미설정 시
로컬 ComfyUI 동작이 그대로 유지되고, remote-comfy일 때만 원격 URL이 필요하다.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

ENV_BACKEND_URL = "PM_BACKEND_URL"
ENV_BACKEND_AUTH = "PM_BACKEND_AUTH"
ENV_STORAGE = "PM_STORAGE"
ENV_ENGINE = "PM_ENGINE"

ENGINE_LOCAL = "local-comfy"
ENGINE_REMOTE = "remote-comfy"
ENGINE_NAMES = (ENGINE_LOCAL, ENGINE_REMOTE)

# 잡 빌드 시점 분기 — 엔진 구현이 아니라 로컬/원격 성격만 구분한다
EngineFlavor = Literal["local", "remote"]

# 원격 백엔드 그룹 → 환경 변수. 그룹은 모델 프로파일의 remote.backend_group과 짝을 이룬다
REMOTE_URL_ENVS = {
    "qwen": "PM_REMOTE_URL_QWEN",
    "flux2": "PM_REMOTE_URL_FLUX2",
}

DEFAULTS = {
    ENV_BACKEND_URL: "http://127.0.0.1:8188",
    ENV_BACKEND_AUTH: "",
    ENV_STORAGE: "./out",
    ENV_ENGINE: ENGINE_LOCAL,
}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    backend_url: str
    backend_auth: str
    storage: Path
    engine: str = ENGINE_LOCAL
    remote_urls: dict[str, str] = field(default_factory=dict)

    @property
    def engine_flavor(self) -> EngineFlavor:
        """빌드 시점 분기용 — 엔진 구현이 아니라 로컬/원격 성격만 노출한다."""
        return "remote" if self.engine == ENGINE_REMOTE else "local"


def _load_remote_urls(src: Mapping[str, str]) -> dict[str, str]:
    urls: dict[str, str] = {}
    for group, env_key in REMOTE_URL_ENVS.items():
        raw = src.get(env_key, "").strip().rstrip("/")
        if not raw:
            continue
        # 원격은 공용 인터넷 경유 — 평문 http는 토큰이 노출되므로 거부한다
        if not raw.startswith("https://"):
            raise ConfigError(f"{env_key}는 https URL이어야 합니다: {raw!r}")
        urls[group] = raw
    return urls


def load_config(env: Mapping[str, str] | None = None) -> Config:
    src: Mapping[str, str] = os.environ if env is None else env
    backend_url = src.get(ENV_BACKEND_URL, DEFAULTS[ENV_BACKEND_URL]).rstrip("/")
    if not backend_url.startswith(("http://", "https://")):
        raise ConfigError(f"{ENV_BACKEND_URL}은 http(s) URL이어야 합니다: {backend_url!r}")
    storage = src.get(ENV_STORAGE, DEFAULTS[ENV_STORAGE]).strip()
    if not storage:
        raise ConfigError(f"{ENV_STORAGE}가 비어 있습니다")
    engine = src.get(ENV_ENGINE, DEFAULTS[ENV_ENGINE]).strip() or DEFAULTS[ENV_ENGINE]
    if engine not in ENGINE_NAMES:
        raise ConfigError(f"{ENV_ENGINE}는 {list(ENGINE_NAMES)} 중 하나여야 합니다: {engine!r}")
    remote_urls = _load_remote_urls(src)
    if engine == ENGINE_REMOTE and not remote_urls:
        raise ConfigError(
            f"{ENGINE_REMOTE} 엔진에는 원격 URL이 최소 1개 필요합니다 "
            f"({', '.join(REMOTE_URL_ENVS.values())})"
        )
    return Config(
        backend_url=backend_url,
        backend_auth=src.get(ENV_BACKEND_AUTH, DEFAULTS[ENV_BACKEND_AUTH]),
        storage=Path(storage),
        engine=engine,
        remote_urls=remote_urls,
    )
