"""LocalComfy 어댑터 — PM_BACKEND_URL의 ComfyUI에 접속한다.

HTTP 코어는 ComfyHTTPEngine이 담당하고, 여기에는 로컬 전용 요소만 남는다.
서버가 미기동이고 온디맨드 레이어(comfy_up.sh)가 감지되면 기동하고,
run/last_active 하트비트로 idle-watchdog가 생성 중 서버를 내리지 않게 한다.
"""

import logging
import os
import subprocess
import time
from pathlib import Path

import httpx

from piemgmaker.config import ENGINE_LOCAL, Config
from piemgmaker.engine.comfy_http import ComfyHTTPEngine
from piemgmaker.engine.contract import EngineError

log = logging.getLogger(__name__)

ENV_ONDEMAND_DIR = "PM_COMFY_ONDEMAND_DIR"
DEFAULT_ONDEMAND_DIR = Path.home() / "comfy-ondemand"


class LocalComfyEngine(ComfyHTTPEngine):
    name = ENGINE_LOCAL

    def __init__(
        self,
        config: Config,
        client: httpx.Client | None = None,
        ondemand_dir: Path | None = None,
    ):
        super().__init__()
        if client is None:
            headers = {}
            if config.backend_auth:
                headers["Authorization"] = f"Bearer {config.backend_auth}"
            # read를 길게: 대형 모델 로드가 서버 이벤트 루프를 막아도 폴링이 끊기지 않게
            client = httpx.Client(
                base_url=config.backend_url,
                headers=headers,
                timeout=httpx.Timeout(connect=10, read=180, write=30, pool=30),
            )
        self._client = client
        env_dir = os.environ.get(ENV_ONDEMAND_DIR)
        self._ondemand_dir = ondemand_dir or (Path(env_dir) if env_dir else DEFAULT_ONDEMAND_DIR)

    def _client_for(self, group: str | None = None) -> httpx.Client:
        # 로컬은 단일 백엔드 — 그룹 라우팅이 없다
        return self._client

    def _on_activity(self) -> None:
        self._touch_active()

    def _touch_active(self) -> None:
        # 온디맨드 레이어가 없으면(외부 서버 접속) 하트비트를 남기지 않는다
        if not self._ondemand_dir.exists():
            return
        run_dir = self._ondemand_dir / "run"
        try:
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "last_active").write_text(str(int(time.time())))
        except OSError as exc:
            # 하트비트 실패는 잡을 죽일 사유가 아니다 — 경고만 남긴다
            log.warning("idle-watchdog 하트비트 갱신 실패: %s", exc)

    def ensure_up(self, group: str | None = None, timeout_s: int = 180) -> None:
        if self._is_up():
            return
        up_script = self._ondemand_dir / "comfy_up.sh"
        if not up_script.is_file():
            raise EngineError(f"ComfyUI 미기동이고 기동 스크립트가 없습니다: {up_script}")
        subprocess.run(["/bin/bash", str(up_script)], check=True, capture_output=True)
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self._is_up():
                return
            time.sleep(2)
        raise EngineError(f"ComfyUI 기동 대기 초과({timeout_s}s)")
