"""RemoteComfy 어댑터 — 원격 ComfyUI 백엔드(HTTPS + Bearer)에 접속한다.

모델 계열별로 서비스가 분리돼 있어 잡의 backend_group으로 클라이언트를 고른다.
서비스는 유휴 시 0으로 축소되므로 첫 요청이 인스턴스를 깨우고, ensure_up이
/system_stats가 응답할 때까지 콜드 스타트를 기다린다.
"""

import logging
import time

import httpx

from piemgmaker.config import ENGINE_REMOTE, Config
from piemgmaker.engine.comfy_http import ComfyHTTPEngine
from piemgmaker.engine.contract import EngineError

log = logging.getLogger(__name__)

DEFAULT_COLD_START_TIMEOUT_S = 600.0
DEFAULT_PROBE_INTERVAL_S = 3.0


class RemoteComfyEngine(ComfyHTTPEngine):
    name = ENGINE_REMOTE
    # 유휴 인스턴스를 깨우면 GPU 과금이 시작된다 — 상태 프로브는 호출자가 판단해서 건너뛴다
    probe_may_wake = True

    def __init__(
        self,
        config: Config,
        transport: httpx.BaseTransport | None = None,
        cold_start_timeout_s: float = DEFAULT_COLD_START_TIMEOUT_S,
        probe_interval_s: float = DEFAULT_PROBE_INTERVAL_S,
    ):
        super().__init__()
        headers = {}
        if config.backend_auth:
            headers["Authorization"] = f"Bearer {config.backend_auth}"
        # 생성자에서 네트워크를 건드리지 않는다 — 클라이언트만 만들어 둔다
        self._clients = {
            group: httpx.Client(
                base_url=url,
                headers=headers,
                transport=transport,
                timeout=httpx.Timeout(connect=10, read=180, write=30, pool=30),
            )
            for group, url in config.remote_urls.items()
        }
        self._cold_start_timeout_s = cold_start_timeout_s
        self._probe_interval_s = probe_interval_s

    def _resolve_group(self, group: str | None) -> str:
        if group is None:
            # 잡에 그룹이 없는 경로(상태 프로브 등)는 설정된 첫 그룹을 대표로 쓴다
            group = next(iter(self._clients), None)
            if group is None:
                raise EngineError(
                    "원격 백엔드 URL이 설정되지 않았습니다 — PM_REMOTE_URL_* 를 확인하세요"
                )
        if group not in self._clients:
            raise EngineError(
                f"원격 백엔드 그룹 {group!r}의 URL이 설정되지 않았습니다 "
                f"(설정된 그룹: {sorted(self._clients)})"
            )
        return group

    def _client_for(self, group: str | None = None) -> httpx.Client:
        return self._clients[self._resolve_group(group)]

    def ensure_up(self, group: str | None = None) -> None:
        """콜드 스타트 대기 — 첫 요청이 인스턴스를 깨우고 기동까지 폴링한다."""
        group = self._resolve_group(group)
        client = self._clients[group]
        deadline = time.time() + self._cold_start_timeout_s
        logged = False
        while True:
            if self._is_up(group):
                return
            if time.time() >= deadline:
                raise EngineError(
                    f"원격 백엔드 기동 대기 초과({self._cold_start_timeout_s}s): "
                    f"group={group} url={client.base_url}"
                )
            if not logged:
                log.info("원격 백엔드 콜드 스타트 대기 중: group=%s url=%s", group, client.base_url)
                logged = True
            time.sleep(self._probe_interval_s)
