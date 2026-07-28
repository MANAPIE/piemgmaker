import json
from io import BytesIO

import httpx
import pytest
from PIL import Image

from piemgmaker.config import load_config
from piemgmaker.engine.contract import (
    EngineDisabledError,
    JobFailedError,
    ProtectedAssetRoutingError,
)
from piemgmaker.engine.hosted_api import HostedAPIEngine
from piemgmaker.engine.local_comfy import LocalComfyEngine
from piemgmaker.schemas.brief import SizeSpec
from piemgmaker.schemas.generation import (
    JobPayload,
    Placement,
    Prompts,
    ResolvedAsset,
    WorkflowRef,
)
from piemgmaker.schemas.style_pack import ModelManifest
from tests.conftest import make_rgba


def make_payload(
    seeds=(1, 2), assets=(), input_images=None, graph=None, output_index=0
) -> JobPayload:
    if graph is None:
        graph = {"5": {"class_type": "KSampler", "inputs": {"seed": 0}}}
    return JobPayload(
        job_id="job-test",
        workflow=WorkflowRef(id="object-gen-v1", hash="h"),
        graph=graph,
        model_manifest=ModelManifest(model_id="m"),
        matting_chain=["segment"],
        seeds=list(seeds),
        size=SizeSpec(width=1024, height=1024),
        prompts=Prompts(positive="p"),
        assets=list(assets),
        input_images=input_images or {},
        output_index=output_index,
    )


PROTECTED = ResolvedAsset(
    asset_id="logo",
    variant_id="main",
    file="f.png",
    file_sha256="0" * 64,
    placement=Placement(x=0, y=0, scale=1.0),
)


class TestHostedAPI:
    def test_보호_자산_잡은_활성화_여부와_무관하게_거부한다(self):
        engine = HostedAPIEngine(enabled=True)
        with pytest.raises(ProtectedAssetRoutingError):
            engine.submit(make_payload(assets=[PROTECTED]))

    def test_기본_비활성이라_일반_잡도_거부한다(self):
        with pytest.raises(EngineDisabledError):
            HostedAPIEngine().submit(make_payload())


def png_bytes() -> bytes:
    buf = BytesIO()
    Image.fromarray(make_rgba(8, 8, (1, 2, 3, 255)), "RGBA").save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def comfy(tmp_path):
    """정상 동작하는 가짜 ComfyUI — submit/poll/fetch 전 구간을 모킹한다."""
    submitted: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/system_stats":
            return httpx.Response(200, json={})
        if path == "/upload/image":
            return httpx.Response(200, json={"name": "uploaded.png", "subfolder": ""})
        if path == "/prompt":
            body = json.loads(request.content)
            submitted.append(body["prompt"])
            return httpx.Response(200, json={"prompt_id": f"p{len(submitted)}"})
        if path.startswith("/history/"):
            remote_id = path.rsplit("/", 1)[1]
            return httpx.Response(
                200,
                json={
                    remote_id: {
                        "status": {"status_str": "success"},
                        "outputs": {
                            "8": {
                                "images": [
                                    {
                                        "filename": f"{remote_id}.png",
                                        "subfolder": "",
                                        "type": "output",
                                    }
                                ]
                            }
                        },
                    }
                },
            )
        if path == "/view":
            return httpx.Response(200, content=png_bytes())
        raise AssertionError(f"예상하지 못한 경로: {path}")

    config = load_config(env={"PM_STORAGE": str(tmp_path / "out")})
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url=config.backend_url)
    engine = LocalComfyEngine(config, client=client, ondemand_dir=tmp_path / "ondemand")
    return engine, submitted


class TestLocalComfy:
    def test_submit은_후보별_시드를_주입해_각각_제출한다(self, comfy):
        engine, submitted = comfy
        handle = engine.submit(make_payload(seeds=(11, 22)))
        assert handle.remote_ids == ["p1", "p2"]
        assert submitted[0]["5"]["inputs"]["seed"] == 11
        assert submitted[1]["5"]["inputs"]["seed"] == 22

    def test_입력_이미지_슬롯은_업로드_후_참조로_치환된다(self, comfy, tmp_path):
        engine, submitted = comfy
        local = tmp_path / "canvas.png"
        local.write_bytes(png_bytes())
        graph = {
            "4": {"class_type": "LoadImage", "inputs": {"image": "__CANVAS__"}},
            "5": {"class_type": "KSampler", "inputs": {"seed": 0}},
        }
        engine.submit(
            make_payload(seeds=(1,), graph=graph, input_images={"canvas": str(local)})
        )
        assert submitted[-1]["4"]["inputs"]["image"] == "uploaded.png"

    def test_poll_done_후_fetch가_시드별_파일을_회수한다(self, comfy, tmp_path):
        engine, _ = comfy
        handle = engine.submit(make_payload(seeds=(11, 22)))
        status = engine.poll(handle)
        assert status.state == "done" and status.done_count == 2
        results = engine.fetch(handle, tmp_path / "cand")
        assert [r.path.name for r in results] == ["00_11.png", "01_22.png"]
        assert all(r.path.is_file() for r in results)

    def test_하트비트가_idle_watchdog_규약대로_기록된다(self, comfy, tmp_path):
        engine, _ = comfy
        (tmp_path / "ondemand").mkdir()  # 온디맨드 레이어가 있을 때만 하트비트를 남긴다
        engine.submit(make_payload(seeds=(1,)))
        assert (tmp_path / "ondemand" / "run" / "last_active").is_file()

    def test_output_index는_다중_출력에서_회수할_장을_고른다(self, tmp_path):
        """네이티브 알파(Layered)는 [컴포지트, 레이어...] 중 마지막 장이 대상이다."""
        viewed: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == "/system_stats":
                return httpx.Response(200, json={})
            if path == "/prompt":
                return httpx.Response(200, json={"prompt_id": "p1"})
            if path.startswith("/history/"):
                images = [
                    {"filename": f"out_{i}.png", "subfolder": "", "type": "output"}
                    for i in range(3)
                ]
                return httpx.Response(
                    200,
                    json={"p1": {"status": {"status_str": "success"}, "outputs": {"9": {"images": images}}}},
                )
            if path == "/view":
                viewed.append(request.url.params["filename"])
                return httpx.Response(200, content=png_bytes())
            raise AssertionError(path)

        config = load_config(env={"PM_STORAGE": str(tmp_path)})
        client = httpx.Client(
            transport=httpx.MockTransport(handler), base_url=config.backend_url
        )
        engine = LocalComfyEngine(config, client=client, ondemand_dir=tmp_path)
        handle = engine.submit(make_payload(seeds=(1,), output_index=-1))
        engine.fetch(handle, tmp_path / "cand")
        assert viewed == ["out_2.png"]

    def test_폴링_중_전송_오류는_일시_장애로_보고_잡을_죽이지_않는다(self, tmp_path):
        """대형 모델 로드가 서버를 막는 동안의 무응답은 running으로 계속 진행한다."""
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == "/system_stats":
                return httpx.Response(200, json={})
            if path == "/prompt":
                return httpx.Response(200, json={"prompt_id": "p1"})
            if path.startswith("/history/"):
                calls["n"] += 1
                raise httpx.ReadTimeout("모델 로드로 무응답")
            raise AssertionError(path)

        config = load_config(env={"PM_STORAGE": str(tmp_path)})
        client = httpx.Client(
            transport=httpx.MockTransport(handler), base_url=config.backend_url
        )
        engine = LocalComfyEngine(config, client=client, ondemand_dir=tmp_path)
        handle = engine.submit(make_payload(seeds=(1,)))
        status = engine.poll(handle)
        assert status.state == "running" and calls["n"] == 1  # 실패 아님, 다음 폴링으로

    def test_엔진_실행_오류는_failed_상태와_메시지로_보고된다(self, tmp_path):
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == "/system_stats":
                return httpx.Response(200, json={})
            if path == "/prompt":
                return httpx.Response(200, json={"prompt_id": "p1"})
            if path.startswith("/history/"):
                return httpx.Response(
                    200,
                    json={
                        "p1": {
                            "status": {
                                "status_str": "error",
                                "messages": [["execution_error"]],
                            },
                            "outputs": {},
                        }
                    },
                )
            raise AssertionError(path)

        config = load_config(env={"PM_STORAGE": str(tmp_path)})
        client = httpx.Client(
            transport=httpx.MockTransport(handler), base_url=config.backend_url
        )
        engine = LocalComfyEngine(config, client=client, ondemand_dir=tmp_path)
        handle = engine.submit(make_payload(seeds=(1,)))
        status = engine.poll(handle)
        assert status.state == "failed"
        assert "execution_error" in (status.error or "")

    def test_prompt_거부는_JobFailedError다(self, tmp_path):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/system_stats":
                return httpx.Response(200, json={})
            return httpx.Response(400, json={"error": "bad graph"})

        config = load_config(env={"PM_STORAGE": str(tmp_path)})
        client = httpx.Client(
            transport=httpx.MockTransport(handler), base_url=config.backend_url
        )
        engine = LocalComfyEngine(config, client=client, ondemand_dir=tmp_path)
        with pytest.raises(JobFailedError):
            engine.submit(make_payload(seeds=(1,)))
