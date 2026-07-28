from pathlib import Path

import pytest

from piemgmaker.config import ConfigError, load_config


def test_기본값은_로컬_comfyui와_로컬_out_디렉토리다():
    config = load_config(env={})
    assert config.backend_url == "http://127.0.0.1:8188"
    assert config.backend_auth == ""
    assert config.storage == Path("./out")


def test_3키를_env로_오버라이드한다():
    config = load_config(
        env={
            "PM_BACKEND_URL": "https://gpu.example.run/",
            "PM_BACKEND_AUTH": "token-123",
            "PM_STORAGE": "gs://bucket/prefix",
        }
    )
    assert config.backend_url == "https://gpu.example.run"  # 후행 슬래시 제거
    assert config.backend_auth == "token-123"
    assert config.storage == Path("gs://bucket/prefix")


def test_http가_아닌_backend_url은_거부한다():
    with pytest.raises(ConfigError):
        load_config(env={"PM_BACKEND_URL": "ftp://nope"})


def test_빈_storage는_거부한다():
    with pytest.raises(ConfigError):
        load_config(env={"PM_STORAGE": "  "})
