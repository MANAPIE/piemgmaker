"""piemgmaker 웹 API (FastAPI).

BriefInput 검증은 기존 pydantic 스키마가 그대로 담당한다. 접근 게이트는
Next.js 레이어 소관 — 이 서버는 로컬 내부 호출 전제로 CORS만 좁힌다.
"""

import hashlib
import io
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from piemgmaker import __version__
from piemgmaker.assets_lib.registry import AssetRegistry, AssetRegistryError
from piemgmaker.config import Config
from piemgmaker.pipeline.capabilities import model_capability
from piemgmaker.schemas.brief import SIZE_PRESETS, BriefInput
from piemgmaker.schemas.manifest import build_manifest
from piemgmaker.server.history import scan_jobs
from piemgmaker.server.jobs import JobNotFound, JobStore
from piemgmaker.workflows.render import load_template

DEFAULT_WORKFLOW_IDS = ("object-gen-v1", "object-inpaint-v1")
UPLOAD_MAX_BYTES = 15 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024


class RerunRequest(BaseModel):
    new_seed: bool = True


class SelectRequest(BaseModel):
    indices: list[int] = Field(min_length=1)


class ArchiveRequest(BaseModel):
    variant: str | None = None
    archived: bool = True


def create_app(
    store: JobStore,
    config: Config,
    assets_dir: Path,
    samples_dir: Path | None = None,
    workflow_ids: tuple[str, ...] = DEFAULT_WORKFLOW_IDS,
) -> FastAPI:
    app = FastAPI(title="PIEmgmaker", version=__version__)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.post("/api/jobs")
    def submit(brief: BriefInput) -> dict:
        return {"job_id": store.submit_brief(brief)}

    @app.post("/api/jobs/{job_id}/rerun")
    def rerun(job_id: str, req: RerunRequest) -> dict:
        try:
            return {"job_id": store.rerun(job_id, new_seed=req.new_seed)}
        except JobNotFound:
            raise HTTPException(404, "잡을 찾을 수 없습니다") from None

    @app.get("/api/jobs/{job_id}")
    def detail(job_id: str) -> dict:
        try:
            return store.detail(job_id)
        except JobNotFound:
            raise HTTPException(404, "잡을 찾을 수 없습니다") from None

    @app.get("/api/jobs")
    def history(q: str | None = None, pack: str | None = None, page: int = 1) -> dict:
        return scan_jobs(store.jobs_root, q=q, pack=pack, page=page)

    @app.get("/api/queue")
    def queue() -> dict:
        return store.queue_snapshot()

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str) -> dict:
        try:
            return {"state": store.cancel(job_id)}
        except JobNotFound:
            raise HTTPException(404, "잡을 찾을 수 없습니다") from None

    # 샘플 갤러리 — 정적 빌드에 묶이지 않도록 파일시스템에서 직접 서빙 (승격 즉시 반영)
    @app.get("/api/samples")
    def samples_manifest() -> dict:
        if samples_dir is None:
            return {"samples": []}
        manifest_path = samples_dir / "manifest.json"
        if not manifest_path.is_file():
            return {"samples": []}
        import json

        return json.loads(manifest_path.read_text(encoding="utf-8"))

    @app.get("/api/samples/{name}")
    def sample_image(name: str) -> FileResponse:
        if (
            samples_dir is None
            or "/" in name
            or ".." in name
            or not name.endswith(".png")
            or not (samples_dir / name).is_file()
        ):
            raise HTTPException(404, "샘플이 없습니다")
        return FileResponse(samples_dir / name, media_type="image/png")

    @app.get("/api/jobs/{job_id}/images/{kind}/{name}")
    def image(job_id: str, kind: str, name: str) -> FileResponse:
        try:
            return FileResponse(store.image_path(job_id, kind, name), media_type="image/png")
        except JobNotFound:
            raise HTTPException(404, "이미지를 찾을 수 없습니다") from None

    @app.get("/api/jobs/{job_id}/export")
    def export(job_id: str, indices: str | None = None) -> Response:
        try:
            info = store.detail(job_id)
        except JobNotFound:
            raise HTTPException(404, "잡을 찾을 수 없습니다") from None
        if indices:
            try:
                wanted: set[int] | None = {int(i) for i in indices.split(",")}
            except ValueError:
                raise HTTPException(400, "indices는 쉼표 구분 정수여야 합니다") from None
        else:
            wanted = set(info["selection"]) if info["selection"] else None  # None = 전체
        buffer = io.BytesIO()
        count = 0
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for cand in info["candidates"]:
                if wanted is not None and cand["index"] not in wanted:
                    continue
                rel = cand["final"] or cand["candidate"]
                if not rel:
                    continue
                archive.write(store.jobs_root / job_id / rel, arcname=Path(rel).name)
                count += 1
        if count == 0:
            raise HTTPException(404, "내보낼 산출물이 없습니다")
        return Response(
            buffer.getvalue(),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="PIEmgmaker_{job_id}.zip"'
            },
        )

    @app.post("/api/jobs/{job_id}/select")
    def select(job_id: str, req: SelectRequest) -> dict:
        try:
            store.select(job_id, req.indices)
        except JobNotFound:
            raise HTTPException(404, "잡을 찾을 수 없습니다") from None
        return {"ok": True}

    @app.get("/api/style-packs")
    def style_packs() -> dict:
        return {
            "packs": [
                {
                    "id": p.id,
                    "name": p.display_name,
                    "version": p.version,
                    "material_class": p.material_class,
                    "shadow_policy": p.shadow_policy,
                }
                for p in store.packs.values()
            ],
            "size_presets": {key: list(value) for key, value in SIZE_PRESETS.items()},
        }

    @app.get("/api/models")
    def models() -> dict:
        # supports_* 는 프로파일 원본이 아니라 현재 엔진에서의 유효값이다 —
        # build_job과 같은 model_capability()로 판정한다.
        engine = {"engine": config.engine, "engine_flavor": config.engine_flavor}
        if store.profiles is None:
            return {**engine, "default": None, "models": []}
        return {
            **engine,
            "default": store.profiles.default,
            "models": [
                {
                    "id": p.id,
                    "label": p.label,
                    "supports_styleref": cap.supports_styleref,
                    "supports_inpaint": cap.supports_inpaint,
                    "supports_native_alpha": cap.supports_native_alpha,
                }
                for p, cap in (
                    (p, model_capability(p, config.engine_flavor))
                    for p in store.profiles.profiles
                )
            ],
        }

    registry = AssetRegistry(assets_dir)

    @app.get("/api/assets")
    def assets(include_archived: bool = False) -> dict:
        # 매 요청 로드 — 파일 기반 v1이라 등록 즉시 반영된다
        result = []
        for a in registry.manifest().assets:
            if a.archived and not include_archived:
                continue
            variants = [
                {"id": v.id, "archived": v.archived, "tags": v.tags}
                for v in a.variants
                if include_archived or not v.archived
            ]
            result.append(
                {
                    "id": a.id,
                    "name": a.name,
                    "type": a.type,
                    "archived": a.archived,
                    "variants": variants,
                    "usage": a.usage.model_dump(),
                }
            )
        return {"assets": result}

    async def _read_upload(request: Request, file: UploadFile, limit: int) -> bytes:
        too_large = HTTPException(413, f"파일이 너무 큽니다 (최대 {limit // (1024 * 1024)}MB)")
        # Content-Length는 신뢰 불가 — 초과 시 조기 거절만 하고, 실제 상한은 본문 누적으로 판정한다
        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            raise too_large
        chunks: list[bytes] = []
        total = 0
        while chunk := await file.read(UPLOAD_CHUNK_BYTES):
            total += len(chunk)
            if total > limit:
                raise too_large
            chunks.append(chunk)
        return b"".join(chunks)

    @app.post("/api/assets")
    async def register_asset(
        request: Request,
        file: UploadFile = File(...),
        asset_id: str = Form(...),
        name: str = Form(...),
        asset_type: str = Form(...),
        variant_id: str = Form("main"),
        min_scale: float = Form(0.1),
        clear_space_px: int = Form(0),
    ) -> dict:
        data = await _read_upload(request, file, UPLOAD_MAX_BYTES)
        try:
            asset = registry.register_asset(
                asset_id=asset_id,
                name=name,
                asset_type=asset_type,
                variant_id=variant_id,
                data=data,
                min_scale=min_scale,
                clear_space_px=clear_space_px,
            )
        except (AssetRegistryError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from None
        return {"asset": asset.model_dump(mode="json")}

    @app.post("/api/assets/{asset_id}/variants")
    async def add_variant(
        asset_id: str,
        request: Request,
        file: UploadFile = File(...),
        variant_id: str = Form(...),
    ) -> dict:
        data = await _read_upload(request, file, UPLOAD_MAX_BYTES)
        try:
            asset = registry.add_variant(asset_id, variant_id, data)
        except AssetRegistryError as exc:
            raise HTTPException(422, str(exc)) from None
        return {"asset": asset.model_dump(mode="json")}

    @app.post("/api/assets/{asset_id}/archive")
    def archive_asset(asset_id: str, req: ArchiveRequest) -> dict:
        try:
            asset = registry.set_archived(asset_id, req.variant, req.archived)
        except AssetRegistryError as exc:
            raise HTTPException(404, str(exc)) from None
        return {"asset": asset.model_dump(mode="json")}

    @app.get("/api/assets/{asset_id}/preview/{variant_id}.png")
    def asset_preview(asset_id: str, variant_id: str) -> FileResponse:
        path = assets_dir / asset_id / f"{variant_id}.png"
        if "/" in asset_id or "/" in variant_id or ".." in asset_id or ".." in variant_id:
            raise HTTPException(404, "잘못된 경로")
        if not path.is_file():
            raise HTTPException(404, "자산 파일이 없습니다")
        return FileResponse(path, media_type="image/png")

    @app.get("/api/uploads/{name}")
    def upload_preview(name: str) -> FileResponse:
        """업로드된 참조 이미지 서빙 — 미리보기용."""
        if "/" in name or ".." in name or not name.endswith(".png"):
            raise HTTPException(404, "업로드 파일이 없습니다")
        path = config.storage / "uploads" / name
        if not path.is_file():
            raise HTTPException(404, "업로드 파일이 없습니다")
        return FileResponse(path, media_type="image/png")

    @app.post("/api/uploads")
    async def upload_reference(request: Request, file: UploadFile = File(...)) -> dict:
        """참조 이미지 업로드 — PNG로 정규화해 PM_STORAGE/uploads에 내용 해시로 저장."""
        from PIL import Image, UnidentifiedImageError

        data = await _read_upload(request, file, UPLOAD_MAX_BYTES)
        try:
            image = Image.open(io.BytesIO(data))
            image.load()
        except (UnidentifiedImageError, OSError) as exc:
            raise HTTPException(422, f"이미지를 읽을 수 없습니다: {exc}") from None
        uploads_dir = config.storage / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(data).hexdigest()[:16]
        path = uploads_dir / f"{digest}.png"
        if not path.is_file():
            image.convert("RGB").save(path)
        return {"path": str(path)}

    @app.get("/api/manifest")
    def manifest() -> dict:
        workflows = [load_template(workflow_id)[1] for workflow_id in workflow_ids]
        report = build_manifest(
            config,
            engine_name=config.engine,
            package_version=__version__,
            workflows=workflows,
            style_packs=store.packs,
        )
        return report.model_dump()

    return app
