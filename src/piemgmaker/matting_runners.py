"""매팅 벤치 러너 — 매팅 전용 그래프를 LocalComfy로 단발 실행한다.

잡 페이로드 규격을 거치지 않는 단발 실행이라 엔진 계약 밖이며, LocalComfyEngine의
저수준 헬퍼를 의도적으로 재사용한다.
"""

import time
import uuid
from io import BytesIO
from pathlib import Path

from PIL import Image

from piemgmaker.config import Config
from piemgmaker.engine.contract import JobFailedError, JobTimeoutError
from piemgmaker.engine.local_comfy import LocalComfyEngine
from piemgmaker.pipeline.matting_router import (
    STRATEGY_FRAGMENTS,
    MattingNodeRegistry,
)
from piemgmaker.schemas.style_pack import MattingStrategyId
from piemgmaker.workflows.render import compose, fill_slots, load_fragment, strip_meta


def matting_only_graph(fragment_name: str, slots: dict[str, object], image_ref: str, prefix: str) -> dict:
    base = {
        "__meta__": {"image_out": "1", "save_node": "2"},
        "1": {"class_type": "LoadImage", "inputs": {"image": image_ref}},
        "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0], "filename_prefix": prefix}},
    }
    graph = compose(base, load_fragment(fragment_name))
    return fill_slots(strip_meta(graph), slots)


class ComfyMattingRunner:
    """bench.StrategyRunner 구현 — 입력 RGB 경로 → RGBA 결과."""

    def __init__(
        self,
        config: Config,
        strategy: MattingStrategyId,
        registry: MattingNodeRegistry | None = None,
        timeout_s: float = 900.0,
    ):
        fragment = STRATEGY_FRAGMENTS[strategy]
        if fragment is None:
            raise ValueError(f"{strategy!r}는 추출형 러너가 없습니다 — 전용 워크플로우로 평가")
        self._fragment = fragment
        self._slots = (registry or MattingNodeRegistry()).get(strategy).slots
        self._engine = LocalComfyEngine(config)
        self._prefix = f"pmbench_{strategy}"
        self._timeout_s = timeout_s

    def __call__(self, rgb_path: Path) -> Image.Image:
        engine = self._engine
        engine.ensure_up()
        image_ref = engine._upload_image(rgb_path)
        graph = matting_only_graph(self._fragment, self._slots, image_ref, self._prefix)
        resp = engine._client.post(
            "/prompt", json={"prompt": graph, "client_id": uuid.uuid4().hex}
        )
        if resp.status_code != 200:
            raise JobFailedError(f"/prompt 거부({resp.status_code}): {resp.text[:1000]}")
        remote_id = resp.json()["prompt_id"]

        deadline = time.time() + self._timeout_s
        entry = None
        while time.time() < deadline:
            time.sleep(1.0)
            candidate = engine._history_entry(remote_id, required=False)
            if candidate is None:
                continue
            status = candidate.get("status", {})
            if status.get("status_str") == "error":
                raise JobFailedError(f"매팅 실행 오류: {status.get('messages')}")
            if candidate.get("outputs"):
                entry = candidate
                break
        if entry is None:
            raise JobTimeoutError(f"매팅 대기 초과({self._timeout_s}s): {remote_id}")

        images = [
            img
            for node_out in entry["outputs"].values()
            for img in node_out.get("images", []) or []
            if img.get("type") != "temp"
        ]
        if not images:
            raise JobFailedError(f"매팅 출력 이미지 없음: {remote_id}")
        img = images[0]
        view = engine._client.get(
            "/view",
            params={
                "filename": img["filename"],
                "subfolder": img.get("subfolder", ""),
                "type": img.get("type", "output"),
            },
        )
        view.raise_for_status()
        return Image.open(BytesIO(view.content)).convert("RGBA")


def default_runners(config: Config, timeout_s: float = 900.0) -> dict[str, ComfyMattingRunner]:
    """추출형 트랙 (a) segment, (c) trimap. (b) native_alpha는 전용 워크플로우 평가."""
    return {
        strategy: ComfyMattingRunner(config, strategy, timeout_s=timeout_s)
        for strategy in ("segment", "trimap")
    }
