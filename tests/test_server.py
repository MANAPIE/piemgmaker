from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from piemgmaker.config import load_config
from piemgmaker.server.app import create_app
from piemgmaker.server.jobs import JobStore
from tests.test_orchestrate import BRIEF_BASE, FakeEngine, blob_512, make_pack


@pytest.fixture
def api(tmp_path):
    config = load_config(env={"PM_STORAGE": str(tmp_path / "out")})
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    (assets_dir / "manifest.yaml").write_text("assets: []\n", encoding="utf-8")
    store = JobStore(
        config,
        {"test-pack": make_pack()},
        assets_dir,
        engine_factory=lambda: FakeEngine(lambda seed: blob_512()),
    )
    client = TestClient(create_app(store, config, assets_dir))
    return client, store


def test_잡_제출부터_완료_조회까지의_기본_흐름(api):
    client, store = api
    res = client.post("/api/jobs", json=BRIEF_BASE)
    assert res.status_code == 200
    job_id = res.json()["job_id"]

    queued = client.get(f"/api/jobs/{job_id}").json()
    assert queued["state"] == "queued"
    assert queued["queue_position"] == 0

    store.process(job_id)  # 워커 대신 동기 실행 (테스트 결정론)

    done = client.get(f"/api/jobs/{job_id}").json()
    assert done["state"] == "done" and done["passed"] is True
    assert len(done["candidates"]) == 2
    assert done["pins"]["workflow"]["id"] == "object-gen-v1"
    assert done["pins"]["style_packs"] == [{"id": "test-pack", "version": "0.1.0"}]

    rel = done["candidates"][0]["final"]
    img = client.get(f"/api/jobs/{job_id}/images/{rel}")
    assert img.status_code == 200
    assert img.headers["content-type"] == "image/png"


def test_브리프_검증_실패는_422다(api):
    client, _ = api
    res = client.post("/api/jobs", json={**BRIEF_BASE, "candidate_count": 99})
    assert res.status_code == 422


def test_확정_선택과_ZIP_내보내기(api):
    client, store = api
    job_id = client.post("/api/jobs", json=BRIEF_BASE).json()["job_id"]
    store.process(job_id)

    assert client.post(f"/api/jobs/{job_id}/select", json={"indices": [1]}).status_code == 200
    exported = client.get(f"/api/jobs/{job_id}/export")
    assert exported.status_code == 200
    assert exported.headers["content-type"] == "application/zip"

    bad = client.get(f"/api/jobs/{job_id}/export", params={"indices": "abc"})
    assert bad.status_code == 400


def test_rerun은_같은_설정으로_새_잡을_만들고_새_시드_옵션을_지원한다(api):
    client, store = api
    job_id = client.post("/api/jobs", json=BRIEF_BASE).json()["job_id"]
    store.process(job_id)

    same = client.post(f"/api/jobs/{job_id}/rerun", json={"new_seed": False}).json()["job_id"]
    fresh = client.post(f"/api/jobs/{job_id}/rerun", json={"new_seed": True}).json()["job_id"]

    same_detail = client.get(f"/api/jobs/{same}").json()
    fresh_detail = client.get(f"/api/jobs/{fresh}").json()
    assert same_detail["rerun_of"] == job_id
    assert same_detail["brief"]["seed"] == 7  # 페이로드 재현용 시드 유지
    assert fresh_detail["brief"]["seed"] == "random"


def test_히스토리는_검색과_팩_필터를_지원한다(api):
    client, store = api
    first = client.post("/api/jobs", json=BRIEF_BASE).json()["job_id"]
    client.post(
        "/api/jobs", json={**BRIEF_BASE, "object_concept": "완전히 다른 무언가"}
    ).json()["job_id"]
    store.process(first)

    everything = client.get("/api/jobs").json()
    assert everything["total"] == 2
    assert everything["items"][0]["thumb"] is None or everything["items"][0]["thumb"].endswith(".png")

    hit = client.get("/api/jobs", params={"q": "글로시"}).json()
    assert hit["total"] == 1

    assert client.get("/api/jobs", params={"pack": "test-pack"}).json()["total"] == 2
    assert client.get("/api/jobs", params={"pack": "nope"}).json()["total"] == 0


def test_없는_잡과_경로_탈출은_404다(api):
    client, store = api
    assert client.get("/api/jobs/unknown12345").status_code == 404
    assert client.post("/api/jobs/unknown12345/rerun", json={}).status_code == 404

    job_id = client.post("/api/jobs", json=BRIEF_BASE).json()["job_id"]
    store.process(job_id)
    assert client.get(f"/api/jobs/{job_id}/images/final/..%2Fpayload.json").status_code == 404
    assert client.get(f"/api/jobs/{job_id}/images/secrets/x.png").status_code == 404


def test_잘못된_형식의_job_id는_경로_결합_전에_404다(api):
    client, _ = api
    # 비-hex·길이 불일치·경로 문자는 잡 디렉토리 경로에 닿기 전에 거절된다
    for bad in ("zzz", "zzzzzzzzzzzz", "0123456789", "..%2Fx"):
        assert client.get(f"/api/jobs/{bad}").status_code == 404
        assert client.post(f"/api/jobs/{bad}/cancel").status_code == 404
        assert client.post(f"/api/jobs/{bad}/select", json={"indices": [1]}).status_code == 404
    # 형식은 맞지만 존재하지 않는 잡도 404 (회귀 방지)
    assert client.get("/api/jobs/0123456789ab").status_code == 404


def test_상한을_초과한_업로드는_413이다(api, monkeypatch):
    client, _ = api
    import piemgmaker.server.app as app_module

    monkeypatch.setattr(app_module, "UPLOAD_MAX_BYTES", 1024)
    oversized = ("file", ("big.png", b"\0" * 4096, "image/png"))
    assert client.post("/api/uploads", files=[oversized]).status_code == 413


def test_서버_재시작_시_미완_잡은_failed_처리된다(tmp_path):
    config = load_config(env={"PM_STORAGE": str(tmp_path / "out")})
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    (assets_dir / "manifest.yaml").write_text("assets: []\n", encoding="utf-8")
    packs = {"test-pack": make_pack()}

    from piemgmaker.schemas.brief import BriefInput

    first = JobStore(config, packs, assets_dir, engine_factory=lambda: None)
    job_id = first.submit_brief(BriefInput.model_validate(BRIEF_BASE))
    assert first.detail(job_id)["state"] == "queued"

    restarted = JobStore(config, packs, assets_dir, engine_factory=lambda: None)
    detail = restarted.detail(job_id)
    assert detail["state"] == "failed"
    assert "재시작" in detail["error"]


def test_style_packs와_manifest_라우트(api):
    client, _ = api
    packs = client.get("/api/style-packs").json()
    assert packs["packs"][0]["id"] == "test-pack"
    assert "1:1" in packs["size_presets"]

    manifest = client.get("/api/manifest").json()
    assert manifest["service"] == "piemgmaker"
    assert len(manifest["workflows"]) == 2

    assert client.get("/api/assets").json() == {"assets": []}


def _png_upload(color=(20, 40, 200, 255)):
    import io

    from PIL import Image

    from tests.conftest import make_rgba

    buf = io.BytesIO()
    Image.fromarray(make_rgba(64, 64, color), "RGBA").save(buf, format="PNG")
    return ("file", ("asset.png", buf.getvalue(), "image/png"))


def test_자산_등록_숨김_복원_웹_흐름(api):
    client, _ = api
    created = client.post(
        "/api/assets",
        files=[_png_upload()],
        data={"asset_id": "badge", "name": "배지", "asset_type": "object", "variant_id": "main"},
    )
    assert created.status_code == 200

    listed = client.get("/api/assets").json()["assets"]
    assert [a["id"] for a in listed] == ["badge"]

    preview = client.get("/api/assets/badge/preview/main.png")
    assert preview.status_code == 200

    # soft delete → 기본 목록에서 사라지고 include_archived로만 보인다
    assert client.post("/api/assets/badge/archive", json={"archived": True}).status_code == 200
    assert client.get("/api/assets").json()["assets"] == []
    archived = client.get("/api/assets", params={"include_archived": True}).json()["assets"]
    assert archived[0]["archived"] is True

    assert client.post("/api/assets/badge/archive", json={"archived": False}).status_code == 200
    assert client.get("/api/assets").json()["assets"][0]["archived"] is False


def test_코어_없는_자산_업로드는_422다(api):
    client, _ = api
    bad = client.post(
        "/api/assets",
        files=[_png_upload(color=(9, 9, 9, 128))],
        data={"asset_id": "soft", "name": "s", "asset_type": "object"},
    )
    assert bad.status_code == 422


def test_참조_이미지_업로드는_경로를_돌려주고_미리보기로_서빙된다(api, tmp_path):
    client, _ = api
    uploaded = client.post("/api/uploads", files=[_png_upload()])
    assert uploaded.status_code == 200
    from pathlib import Path

    path = Path(uploaded.json()["path"])
    assert path.is_file() and path.suffix == ".png"

    preview = client.get(f"/api/uploads/{path.name}")
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/png"
    assert client.get("/api/uploads/..%2Fsecret.png").status_code == 404
    assert client.get("/api/uploads/none.png").status_code == 404


def test_models_라우트는_프로파일_없으면_빈_목록이다(api):
    client, _ = api
    assert client.get("/api/models").json() == {
        "engine": "local-comfy",
        "engine_flavor": "local",
        "default": None,
        "models": [],
    }


def test_models_라우트의_supports는_엔진별_유효값이다(tmp_path):
    """프로파일 원본을 그대로 내보내면 UI가 쓸 수 있다고 표시한 뒤 제출에서 실패한다."""
    from piemgmaker.schemas.model_profile import load_model_profiles
    from piemgmaker.server.app import create_app

    profiles_path = Path(__file__).resolve().parents[1] / "model_profiles.yaml"
    profiles = load_model_profiles(profiles_path)

    def models_for(env: dict[str, str]) -> dict[str, dict]:
        config = load_config(env={"PM_STORAGE": str(tmp_path / "out"), **env})
        assets_dir = tmp_path / "assets"
        assets_dir.mkdir(exist_ok=True)
        (assets_dir / "manifest.yaml").write_text("assets: []\n", encoding="utf-8")
        store = JobStore(
            config,
            {"test-pack": make_pack()},
            assets_dir,
            engine_factory=lambda: FakeEngine(lambda seed: blob_512()),
            profiles=profiles,
        )
        body = TestClient(create_app(store, config, assets_dir)).get("/api/models").json()
        return body["engine_flavor"], {m["id"]: m for m in body["models"]}

    local_flavor, local_models = models_for({})
    remote_flavor, remote_models = models_for(
        {"PM_ENGINE": "remote-comfy", "PM_REMOTE_URL_QWEN": "https://example.invalid"}
    )

    assert (local_flavor, remote_flavor) == ("local", "remote")

    # qwen-image는 로컬에서 참조 이미지·네이티브 알파를 지원하지만 원격에서는 둘 다 못 쓴다
    assert local_models["qwen-image"]["supports_styleref"] is True
    assert local_models["qwen-image"]["supports_native_alpha"] is True
    assert remote_models["qwen-image"]["supports_styleref"] is False
    assert remote_models["qwen-image"]["supports_native_alpha"] is False

    # 생성·인페인팅은 원격에서도 유지된다
    assert remote_models["qwen-image"]["supports_inpaint"] is True


def test_샘플은_API로_서빙돼_빌드_이후_추가분도_반영된다(tmp_path):
    import json

    from piemgmaker.server.app import create_app

    config = load_config(env={"PM_STORAGE": str(tmp_path / "out")})
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    (assets_dir / "manifest.yaml").write_text("assets: []\n", encoding="utf-8")
    samples_dir = tmp_path / "samples"
    samples_dir.mkdir()
    store = JobStore(config, {"test-pack": make_pack()}, assets_dir, engine_factory=lambda: None)
    client = TestClient(create_app(store, config, assets_dir, samples_dir=samples_dir))

    assert client.get("/api/samples").json() == {"samples": []}

    # 서버 기동 후(=빌드 후) 추가된 샘플도 즉시 서빙
    from PIL import Image

    from tests.conftest import make_rgba

    Image.fromarray(make_rgba(16, 16, (1, 2, 3, 255)), "RGBA").save(samples_dir / "new.png")
    (samples_dir / "manifest.json").write_text(
        json.dumps({"samples": [{"id": "new", "file": "new.png"}]}), encoding="utf-8"
    )
    assert client.get("/api/samples").json()["samples"][0]["id"] == "new"
    assert client.get("/api/samples/new.png").status_code == 200
    assert client.get("/api/samples/..%2Fmanifest.json").status_code == 404
    assert client.get("/api/samples/none.png").status_code == 404


def test_대기_잡_취소는_즉시_반영되고_워커는_건너뛴다(api):
    client, store = api
    job_id = client.post("/api/jobs", json=BRIEF_BASE).json()["job_id"]

    res = client.post(f"/api/jobs/{job_id}/cancel")
    assert res.status_code == 200 and res.json()["state"] == "canceled"
    assert client.get("/api/queue").json()["queued"] == []  # 대기열에서 사라짐

    store.process(job_id)  # 워커가 꺼내도 실행하지 않는다
    detail = client.get(f"/api/jobs/{job_id}").json()
    assert detail["state"] == "canceled"
    assert detail["candidates"] == []

    assert client.post("/api/jobs/unknown9999/cancel").status_code == 404


def test_실행_중_취소는_플래그로_전달되고_canceled로_종결된다(api):
    client, store = api
    job_id = client.post("/api/jobs", json=BRIEF_BASE).json()["job_id"]
    # 실행 직전 취소 플래그를 심어 폴링 루프 진입 시 취소되는 경로를 검증
    (store.jobs_root / job_id / "cancel").write_text("1", encoding="utf-8")
    store.process(job_id)
    detail = client.get(f"/api/jobs/{job_id}").json()
    assert detail["state"] == "canceled"
    assert "취소" in detail["error"]


def test_웹_잡의_후보_프롬프트는_외부_작업으로_세지_않는다():
    entry_web = [0, "p1", {"9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "pm_abc123"}}}]
    entry_cli = [1, "p2", {"9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "pmbench_segment"}}}]
    assert JobStore._entry_prefix(entry_web) == "pm_abc123"
    assert JobStore._entry_prefix(entry_cli) == "pmbench_segment"
    assert JobStore._entry_prefix([0, "p3", {}]) is None
    # 분류: 활성 웹 잡 abc123의 프롬프트는 내부, 나머지는 외부
    web_prefixes = {"pm_abc123"}
    entries = [entry_web, entry_web, entry_cli]
    external = sum(1 for e in entries if JobStore._entry_prefix(e) not in web_prefixes)
    assert external == 1


def test_대기열_항목에는_프롬프트_정보가_포함된다(api):
    client, _ = api
    client.post(
        "/api/jobs",
        json={**BRIEF_BASE, "style": {"style_packs": ["test-pack"], "free_text": "빈티지 무드"}},
    )
    item = client.get("/api/queue").json()["queued"][0]
    assert item["object_concept"] == BRIEF_BASE["object_concept"]
    assert item["style_packs"] == ["test-pack"]
    assert item["free_text"] == "빈티지 무드"
    assert item["seed"] == 7 and item["candidate_count"] == 2


def test_queue_스냅샷은_실행중과_대기열을_순번과_함께_보여준다(api):
    client, store = api
    first = client.post("/api/jobs", json=BRIEF_BASE).json()["job_id"]
    second = client.post(
        "/api/jobs", json={**BRIEF_BASE, "object_concept": "두 번째 잡"}
    ).json()["job_id"]

    snapshot = client.get("/api/queue").json()
    assert snapshot["running"] is None
    assert [j["job_id"] for j in snapshot["queued"]] == [first, second]
    assert [j["queue_position"] for j in snapshot["queued"]] == [0, 1]
    assert snapshot["queued"][1]["object_concept"] == "두 번째 잡"

    store.process(first)
    store.process(second)
    done = client.get("/api/queue").json()
    assert done["running"] is None and done["queued"] == []
    assert "engine" in done  # 엔진 상태 동봉 (미기동이면 reachable=False)
