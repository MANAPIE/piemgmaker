"""원격 백엔드(remote-comfy) 경로 — 설정·엔진 팩토리·그룹 라우팅·빌드·상태 프로브."""

import json
from pathlib import Path

import httpx
import pytest
from PIL import Image

from piemgmaker.config import Config, ConfigError, load_config
from piemgmaker.engine import ENGINE_REGISTRY, create_engine
from piemgmaker.engine.contract import EngineError, JobHandle
from piemgmaker.engine.local_comfy import LocalComfyEngine
from piemgmaker.engine.remote_comfy import RemoteComfyEngine
from piemgmaker.pipeline.orchestrate import OrchestrationError, build_job
from piemgmaker.schemas.brief import BriefInput
from piemgmaker.schemas.model_profile import (
    ModelProfileRegistry,
    RemoteFileOverride,
    RemoteSpec,
    load_model_profiles,
)
from piemgmaker.server.jobs import JobStore
from tests.conftest import make_rgba
from tests.test_engine import make_payload
from tests.test_model_profiles import make_registry
from tests.test_orchestrate import BRIEF_BASE, make_pack

REPO_ROOT = Path(__file__).parent.parent

REMOTE_ENV = {
    "PM_ENGINE": "remote-comfy",
    "PM_BACKEND_AUTH": "test-token",
    "PM_REMOTE_URL_QWEN": "https://qwen.example.run",
    "PM_REMOTE_URL_FLUX2": "https://flux2.example.run",
}


def remote_config(tmp_path: Path, **extra: str) -> Config:
    return load_config(env={**REMOTE_ENV, "PM_STORAGE": str(tmp_path), **extra})


def remote_engine(config: Config, handler, **kwargs) -> RemoteComfyEngine:
    return RemoteComfyEngine(
        config, transport=httpx.MockTransport(handler), probe_interval_s=0.0, **kwargs
    )


class TestConfig:
    def test_PM_ENGINE_기본값은_로컬_엔진이다(self):
        config = load_config(env={})
        assert config.engine == "local-comfy"
        assert config.engine_flavor == "local"

    def test_원격_엔진은_그룹별_URL을_읽고_후행_슬래시를_제거한다(self, tmp_path):
        config = remote_config(tmp_path, PM_REMOTE_URL_QWEN="https://qwen.example.run/")
        assert config.remote_urls == {
            "qwen": "https://qwen.example.run",
            "flux2": "https://flux2.example.run",
        }

    def test_원격_URL이_하나도_없으면_원격_엔진_설정을_거부한다(self):
        with pytest.raises(ConfigError, match="원격 URL"):
            load_config(env={"PM_ENGINE": "remote-comfy"})

    def test_평문_http_원격_URL은_거부한다(self):
        with pytest.raises(ConfigError, match="https"):
            load_config(
                env={"PM_ENGINE": "remote-comfy", "PM_REMOTE_URL_QWEN": "http://qwen.example.run"}
            )

    def test_알_수_없는_PM_ENGINE_값은_거부한다(self):
        with pytest.raises(ConfigError, match="PM_ENGINE"):
            load_config(env={"PM_ENGINE": "hosted-api"})


class TestEngineFactory:
    def test_레지스트리_이름으로_엔진을_만든다(self, tmp_path):
        local = create_engine(load_config(env={"PM_STORAGE": str(tmp_path)}))
        remote = create_engine(remote_config(tmp_path))
        assert isinstance(local, LocalComfyEngine)
        assert isinstance(remote, RemoteComfyEngine)

    def test_레지스트리에_없는_엔진은_ConfigError다(self, tmp_path):
        config = Config(
            backend_url="http://127.0.0.1:8188",
            backend_auth="",
            storage=tmp_path,
            engine="sdxl-cloud",
        )
        with pytest.raises(ConfigError, match="알 수 없는 엔진"):
            create_engine(config)

    def test_레지스트리_키는_어댑터_이름과_일치한다(self):
        assert all(name == engine_cls.name for name, engine_cls in ENGINE_REGISTRY.items())


class TestRemoteRouting:
    def test_잡의_backend_group이_해당_그룹_URL로_제출된다(self, tmp_path):
        hosts: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            hosts.append(request.url.host)
            if request.url.path == "/system_stats":
                return httpx.Response(200, json={})
            if request.url.path == "/prompt":
                return httpx.Response(200, json={"prompt_id": "p1"})
            raise AssertionError(request.url.path)

        engine = remote_engine(remote_config(tmp_path), handler)
        payload = make_payload(seeds=(1,)).model_copy(update={"backend_group": "flux2"})
        handle = engine.submit(payload)
        assert set(hosts) == {"flux2.example.run"}
        assert handle.engine_group == "flux2"

    def test_모든_요청에_Bearer_인증_헤더가_붙는다(self, tmp_path):
        seen: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers.get("authorization"))
            return httpx.Response(200, json={"queue_running": [], "queue_pending": []})

        engine = remote_engine(remote_config(tmp_path), handler)
        engine.queue_snapshot("qwen")
        assert seen == ["Bearer test-token"]

    def test_URL이_설정되지_않은_그룹은_명확한_오류다(self, tmp_path):
        config = load_config(
            env={
                "PM_ENGINE": "remote-comfy",
                "PM_REMOTE_URL_QWEN": "https://qwen.example.run",
                "PM_STORAGE": str(tmp_path),
            }
        )
        engine = remote_engine(config, lambda request: httpx.Response(200, json={}))
        payload = make_payload(seeds=(1,)).model_copy(update={"backend_group": "flux2"})
        with pytest.raises(EngineError, match="flux2"):
            engine.submit(payload)

    def test_큐_조회는_기동_대기를_거치지_않는다(self, tmp_path):
        """유휴 인스턴스를 깨우지 않으려면 상태 조회가 /system_stats를 건드리면 안 된다."""
        paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            paths.append(request.url.path)
            return httpx.Response(200, json={"queue_running": [[0, "p1", {}]], "queue_pending": []})

        engine = remote_engine(remote_config(tmp_path), handler)
        snapshot = engine.queue_snapshot("qwen")
        assert paths == ["/queue"]
        assert snapshot["reachable"] and snapshot["running"] == 1

    def test_큐_응답이_JSON이_아니면_도달_불가로_흡수한다(self, tmp_path):
        """프록시 오류 페이지(비-JSON 200)가 상태 패널 500으로 전파되면 안 된다."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>bad gateway</html>")

        engine = remote_engine(remote_config(tmp_path), handler)
        assert engine.queue_snapshot("qwen")["reachable"] is False


class TestColdStart:
    def test_기동_전_503은_기다렸다가_200에서_진행한다(self, tmp_path):
        responses = [503, 503, 200]

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/system_stats"
            return httpx.Response(responses.pop(0), json={})

        engine = remote_engine(remote_config(tmp_path), handler)
        engine.ensure_up("qwen")
        assert responses == []  # 세 번째 응답까지 폴링했다

    def test_기동_대기_초과는_EngineError다(self, tmp_path):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={})

        engine = remote_engine(remote_config(tmp_path), handler, cold_start_timeout_s=0.0)
        with pytest.raises(EngineError, match="기동 대기 초과"):
            engine.ensure_up("qwen")


class TestCancel:
    def test_취소는_대기_프롬프트를_지우고_실행_중이면_인터럽트한다(self, tmp_path):
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(f"{request.method} {request.url.path}")
            if request.method == "GET" and request.url.path == "/queue":
                return httpx.Response(
                    200, json={"queue_running": [[0, "p1", {}]], "queue_pending": []}
                )
            return httpx.Response(200, json={})

        engine = remote_engine(remote_config(tmp_path), handler)
        handle = JobHandle(
            engine="remote-comfy",
            job_id="j1",
            seeds=[1],
            remote_ids=["p1"],
            engine_group="qwen",
        )
        engine.cancel(handle)
        assert calls == ["POST /queue", "GET /queue", "POST /interrupt"]


class TestPollFailurePromotion:
    """전송 실패가 쌓이면 서버 생사를 확인한다 — 원격 장애 시 큐가 몇 시간 멈추지 않게."""

    @staticmethod
    def _handle() -> JobHandle:
        return JobHandle(
            engine="remote-comfy", job_id="j1", seeds=[1], remote_ids=["p1"], engine_group="qwen"
        )

    def test_연속_실패_4회까지는_일시_장애로_보고_진행한다(self, tmp_path):
        probes: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            probes.append(request.url.path)
            raise httpx.ReadTimeout("무응답")

        engine = remote_engine(remote_config(tmp_path), handler)
        handle = self._handle()
        states = [engine.poll(handle).state for _ in range(4)]
        assert states == ["running"] * 4
        assert "/system_stats" not in probes  # 아직 생사 확인 단계가 아니다

    def test_연속_실패_5회에_서버도_죽었으면_EngineError로_승격한다(self, tmp_path):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("무응답")

        engine = remote_engine(remote_config(tmp_path), handler)
        handle = self._handle()
        for _ in range(4):
            assert engine.poll(handle).state == "running"
        with pytest.raises(EngineError, match="연속 폴링 실패"):
            engine.poll(handle)

    def test_연속_실패_후에도_서버가_살아_있으면_계속_진행한다(self, tmp_path):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/system_stats":
                return httpx.Response(200, json={})
            raise httpx.ReadTimeout("모델 로드로 무응답")

        engine = remote_engine(remote_config(tmp_path), handler)
        handle = self._handle()
        states = [engine.poll(handle).state for _ in range(6)]
        assert states == ["running"] * 6


QWEN_REMOTE = RemoteSpec(
    backend_group="qwen",
    files=[RemoteFileOverride(replaces="qwen.gguf", name="qwen-q6.gguf")],
)


def registry_with_remote(spec: RemoteSpec = QWEN_REMOTE) -> ModelProfileRegistry:
    """qwen-image에만 remote 블록을 단 레지스트리 (flux2-dev는 원격 미지원 케이스로 남긴다)."""
    base = make_registry()
    profiles = [
        p.model_copy(update={"remote": spec}) if p.id == "qwen-image" else p
        for p in base.profiles
    ]
    return base.model_copy(update={"profiles": profiles})


class TestRemoteBuild:
    def test_원격_빌드는_모델_파일을_치환하고_backend_group을_기록한다(self):
        build = build_job(
            BriefInput.model_validate(BRIEF_BASE),
            {"test-pack": make_pack()},
            profiles=registry_with_remote(),
            engine_flavor="remote",
        )
        payload = build.payload_for(None, build.seeds)
        assert [f.name for f in payload.model_manifest.files] == [
            "qwen-q6.gguf",
            "qwen_te.safetensors",
            "qwen_vae.safetensors",
        ]
        assert payload.backend_group == "qwen"

    def test_치환된_파일명이_그래프의_모델_슬롯에_들어간다(self):
        build = build_job(
            BriefInput.model_validate(BRIEF_BASE),
            {"test-pack": make_pack()},
            profiles=registry_with_remote(),
            engine_flavor="remote",
        )
        assert build.graph_for(None)["1"]["inputs"]["unet_name"] == "qwen-q6.gguf"

    def test_기본_플레이버는_원격_블록을_무시한다(self):
        build = build_job(
            BriefInput.model_validate(BRIEF_BASE),
            {"test-pack": make_pack()},
            profiles=registry_with_remote(),
        )
        payload = build.payload_for(None, build.seeds)
        assert payload.model_manifest.files[0].name == "qwen.gguf"
        assert payload.backend_group is None

    def test_remote_블록이_없는_프로파일은_원격_실행을_거부한다(self):
        brief = BriefInput.model_validate({**BRIEF_BASE, "model": "flux2-dev"})
        with pytest.raises(OrchestrationError, match="원격 백엔드를 지원하지 않습니다"):
            build_job(
                brief,
                {"test-pack": make_pack()},
                profiles=registry_with_remote(),
                engine_flavor="remote",
            )

    def test_원격에서_native_alpha_미지원이면_trimap으로_강등된다(self):
        pack = make_pack(
            material_class="translucent", matting={"strategy": "native_alpha", "model": "m"}
        )
        build = build_job(
            BriefInput.model_validate(BRIEF_BASE),
            {"test-pack": pack},
            profiles=registry_with_remote(),
            engine_flavor="remote",
        )
        assert build.chain == ["trimap"]
        assert build.primary.workflow_ref.id == "object-gen-qwen-v1"

    def test_원격_미지원_styleref_요청은_명확한_오류다(self, tmp_path):
        ref = tmp_path / "ref.png"
        Image.fromarray(make_rgba(64, 64, (200, 30, 30, 255)), "RGBA").save(ref)
        brief = BriefInput.model_validate({**BRIEF_BASE, "reference_images": [str(ref)]})
        with pytest.raises(OrchestrationError, match="원격 백엔드는 참조 이미지"):
            build_job(
                brief,
                {"test-pack": make_pack()},
                profiles=registry_with_remote(),
                workdir=tmp_path / "in",
                engine_flavor="remote",
            )

    def test_치환_대상이_어느_매니페스트에도_없으면_로드_시점에_거부된다(self):
        # apply()가 매칭만 적용하는 대신 오타 탐지는 프로파일 로드(model_validate)가 맡는다 —
        # 잡 빌드보다 이른 시점이라 잘못된 프로파일 파일이 서버 기동 자체를 막는다.
        base = make_registry()
        data = base.model_dump()
        data["profiles"][0]["remote"] = {
            "backend_group": "qwen",
            "files": [{"replaces": "없는파일.gguf", "name": "q6.gguf"}],
        }
        with pytest.raises(ValueError, match="어느 매니페스트에도 없습니다"):
            ModelProfileRegistry.model_validate(data)

    def test_치환은_styleref와_native_alpha_매니페스트에도_각각_적용된다(self, tmp_path):
        spec = RemoteSpec(
            backend_group="qwen",
            files=[
                RemoteFileOverride(replaces="qwen.gguf", name="qwen-q6.gguf"),
                RemoteFileOverride(replaces="qwen_edit.gguf", name="edit-q6.gguf"),
                RemoteFileOverride(replaces="layered.gguf", name="layered-q6.gguf"),
            ],
            supports_styleref=True,
            supports_native_alpha=True,
        )
        # styleref 경로 — edit unet만 치환되고 공유 TE·VAE는 그대로다
        ref = tmp_path / "ref.png"
        Image.fromarray(make_rgba(64, 64, (200, 30, 30, 255)), "RGBA").save(ref)
        brief = BriefInput.model_validate({**BRIEF_BASE, "reference_images": [str(ref)]})
        build = build_job(
            brief,
            {"test-pack": make_pack()},
            profiles=registry_with_remote(spec),
            workdir=tmp_path / "in",
            engine_flavor="remote",
        )
        payload = build.payload_for(None, build.seeds)
        assert [f.name for f in payload.model_manifest.files] == [
            "edit-q6.gguf",
            "qwen_te.safetensors",
            "qwen_vae.safetensors",
        ]

        # native_alpha 경로 — 강등 없이 전용 워크플로우와 layered 치환본으로 빌드된다
        pack = make_pack(
            material_class="translucent", matting={"strategy": "native_alpha", "model": "m"}
        )
        build = build_job(
            BriefInput.model_validate(BRIEF_BASE),
            {"test-pack": pack},
            profiles=registry_with_remote(spec),
            engine_flavor="remote",
        )
        assert build.chain[0] == "native_alpha"
        assert build.primary.workflow_ref.id == "object-gen-native-alpha-v1"
        payload = build.payload_for(None, build.seeds)
        assert payload.model_manifest.files[0].name == "layered-q6.gguf"
        # trimap 폴백은 base 치환본으로 돌아간다
        assert build.fallback is not None
        fb_payload = build.payload_for("trimap", build.seeds)
        assert fb_payload.model_manifest.files[0].name == "qwen-q6.gguf"

    def test_프로파일_레지스트리_없이는_원격_실행을_거부한다(self):
        with pytest.raises(OrchestrationError, match="모델 프로파일"):
            build_job(
                BriefInput.model_validate(BRIEF_BASE),
                {"test-pack": make_pack()},
                engine_flavor="remote",
            )


def test_리포_프로파일은_두_계열_모두_원격_그룹과_파일_셋을_선언한다():
    registry = load_model_profiles(REPO_ROOT / "model_profiles.yaml")
    qwen = registry.get("qwen-image")
    flux2 = registry.get("flux2-dev")
    assert (qwen.remote.backend_group, flux2.remote.backend_group) == ("qwen", "flux2")
    # qwen은 원격 GPU 용량에 맞춰 세 매니페스트(base·styleref·native_alpha)의 unet을 Q6_K로 치환한다
    assert [f.name for f in qwen.remote.apply(qwen.manifest).files][0] == "Qwen_Image-Q6_K.gguf"
    assert (
        qwen.remote.apply(qwen.styleref_manifest).files[0].name == "qwen-image-edit-2511-Q6_K.gguf"
    )
    assert (
        qwen.remote.apply(qwen.native_alpha.manifest).files[0].name
        == "qwen-image-layered-Q6_K.gguf"
    )
    assert qwen.remote.supports_styleref and qwen.remote.supports_native_alpha
    # flux2는 파일명은 로컬과 같지만 원격 unet이 다른 Q8 빌드(HF 배포본)라 sha만 갈린다
    flux2_remote = flux2.remote.apply(flux2.manifest)
    assert [f.name for f in flux2_remote.files] == [f.name for f in flux2.manifest.files]
    remote_unet = next(f for f in flux2_remote.files if f.name == "flux2-dev-Q8_0.gguf")
    local_unet = next(f for f in flux2.manifest.files if f.name == "flux2-dev-Q8_0.gguf")
    assert remote_unet.sha256 != local_unet.sha256


class WakeCountingEngine:
    """probe_may_wake 엔진 대역 — 네트워크 프로브 호출 횟수를 센다."""

    name = "remote-comfy"
    probe_may_wake = True

    def __init__(self) -> None:
        self.probes = 0
        self.groups: list[str | None] = []

    def queue_snapshot(self, group: str | None = None) -> dict:
        self.probes += 1
        self.groups.append(group)
        return {
            "reachable": True,
            "state": "up",
            "busy": False,
            "running": 0,
            "pending": 0,
            "entries": [],
        }


def make_store(tmp_path: Path, engine) -> JobStore:
    config = load_config(env={"PM_STORAGE": str(tmp_path / "out")})
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir(exist_ok=True)
    (assets_dir / "manifest.yaml").write_text("assets: []\n", encoding="utf-8")
    return JobStore(config, {"test-pack": make_pack()}, assets_dir, engine_factory=lambda: engine)


class TestEngineStatusProbe:
    def test_진행_중_잡이_없으면_원격을_깨우지_않는다(self, tmp_path):
        engine = WakeCountingEngine()
        store = make_store(tmp_path, engine)
        status = store.queue_snapshot()["engine"]
        assert engine.probes == 0
        assert status["state"] == "idle" and status["reachable"] is False

    def test_대기_잡이_있으면_원격_큐를_프로브한다(self, tmp_path):
        engine = WakeCountingEngine()
        store = make_store(tmp_path, engine)
        store.submit_brief(BriefInput.model_validate(BRIEF_BASE))
        status = store.queue_snapshot()["engine"]
        assert engine.probes == 1
        assert status["state"] == "up" and status["reachable"] is True

    def test_큐를_보고하지_않는_엔진은_unknown으로_보고한다(self, tmp_path):
        store = make_store(tmp_path, None)
        assert store.queue_snapshot()["engine"]["state"] == "unknown"

    def test_진행_중_잡의_backend_group으로_프로브한다(self, tmp_path):
        engine = WakeCountingEngine()
        store = make_store(tmp_path, engine)
        job_id = store.submit_brief(BriefInput.model_validate(BRIEF_BASE))
        (store.jobs_root / job_id / "payload.json").write_text(
            json.dumps({"backend_group": "flux2"}), encoding="utf-8"
        )
        store.queue_snapshot()
        assert engine.groups == ["flux2"]
