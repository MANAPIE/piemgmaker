"""config 3키 로더 — 환경 분기는 이 3키 값으로만 발생한다."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

ENV_BACKEND_URL = "PM_BACKEND_URL"
ENV_BACKEND_AUTH = "PM_BACKEND_AUTH"
ENV_STORAGE = "PM_STORAGE"

DEFAULTS = {
    ENV_BACKEND_URL: "http://127.0.0.1:8188",
    ENV_BACKEND_AUTH: "",
    ENV_STORAGE: "./out",
}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    backend_url: str
    backend_auth: str
    storage: Path


def load_config(env: Mapping[str, str] | None = None) -> Config:
    src: Mapping[str, str] = os.environ if env is None else env
    backend_url = src.get(ENV_BACKEND_URL, DEFAULTS[ENV_BACKEND_URL]).rstrip("/")
    if not backend_url.startswith(("http://", "https://")):
        raise ConfigError(f"{ENV_BACKEND_URL}은 http(s) URL이어야 합니다: {backend_url!r}")
    storage = src.get(ENV_STORAGE, DEFAULTS[ENV_STORAGE]).strip()
    if not storage:
        raise ConfigError(f"{ENV_STORAGE}가 비어 있습니다")
    return Config(
        backend_url=backend_url,
        backend_auth=src.get(ENV_BACKEND_AUTH, DEFAULTS[ENV_BACKEND_AUTH]),
        storage=Path(storage),
    )
