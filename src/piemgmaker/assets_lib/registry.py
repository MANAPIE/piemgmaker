"""자산 웹 등록·보관(soft delete) — 파일 기반 라이브러리의 쓰기 경로.

- 등록 검증: PNG·알파 채널·**완전 불투명 코어 존재**(코어 해시 검증 전제)
- manifest.yaml은 락 + 임시파일 → os.replace 원자 교체 (워커의 빌드 시 로드와 경합 방지)
- 삭제는 하지 않는다 — archived 플래그로 신규 사용만 차단(soft delete)
"""

import io
import re
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml
from PIL import Image, UnidentifiedImageError

from piemgmaker.pipeline.paste_back import AssetCoreEmpty, core_mask
from piemgmaker.schemas.asset import Asset, AssetManifest, AssetUsage, AssetVariant

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
MAX_FILE_BYTES = 10 * 1024 * 1024
MIN_SIDE = 16


class AssetRegistryError(ValueError):
    pass


@dataclass
class ValidatedImage:
    image: Image.Image


def _validate_asset_png(data: bytes) -> ValidatedImage:
    if len(data) > MAX_FILE_BYTES:
        raise AssetRegistryError(f"파일이 너무 큽니다 (최대 {MAX_FILE_BYTES // (1024 * 1024)}MB)")
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise AssetRegistryError(f"이미지를 읽을 수 없습니다: {exc}") from exc
    if image.format != "PNG":
        raise AssetRegistryError("PNG만 등록할 수 있습니다 (알파 채널 필요)")
    rgba = image.convert("RGBA")
    if min(rgba.size) < MIN_SIDE:
        raise AssetRegistryError(f"최소 {MIN_SIDE}px 이상이어야 합니다")
    arr = np.asarray(rgba)
    if not (arr[..., 3] < 255).any():
        # 전면 불투명 PNG도 허용하되, 알파 채널 자체가 없던 포맷은 위 PNG 검사로 걸러진다
        pass
    try:
        core_mask(arr)
    except AssetCoreEmpty as exc:
        raise AssetRegistryError(
            "불투명 픽셀이 전혀 없습니다 — 코어 해시 검증이 불가능해 등록할 수 없습니다. "
            "로고·오브젝트를 완전 불투명(반투명 아님)으로 내보낸 뒤 다시 등록하세요"
        ) from exc
    return ValidatedImage(image=rgba)


class AssetRegistry:
    def __init__(self, root: Path):
        self.root = root
        self._lock = threading.Lock()
        self.root.mkdir(parents=True, exist_ok=True)
        if not (self.root / "manifest.yaml").is_file():
            self._write(AssetManifest())

    # ── 조회 ──

    def manifest(self) -> AssetManifest:
        data = yaml.safe_load((self.root / "manifest.yaml").read_text(encoding="utf-8")) or {}
        return AssetManifest.model_validate(data)

    # ── 쓰기 ──

    def register_asset(
        self,
        *,
        asset_id: str,
        name: str,
        asset_type: str,
        variant_id: str,
        data: bytes,
        min_scale: float = 0.1,
        clear_space_px: int = 0,
    ) -> Asset:
        for value, label in ((asset_id, "자산 id"), (variant_id, "variant id")):
            if not ID_RE.fullmatch(value):
                raise AssetRegistryError(f"{label}는 kebab-case여야 합니다: {value!r}")
        validated = _validate_asset_png(data)
        with self._lock:
            manifest = self.manifest()
            if any(a.id == asset_id for a in manifest.assets):
                raise AssetRegistryError(f"이미 존재하는 자산 id입니다: {asset_id!r} (variant 추가를 사용하세요)")
            self._save_file(asset_id, variant_id, validated.image)
            asset = Asset(
                id=asset_id,
                name=name,
                type=asset_type,  # Literal 검증은 pydantic이 수행
                variants=[AssetVariant(id=variant_id, file=f"{asset_id}/{variant_id}.png")],
                usage=AssetUsage(min_scale=min_scale, clear_space_px=clear_space_px),
            )
            manifest.assets.append(asset)
            self._write(manifest)
            return asset

    def add_variant(self, asset_id: str, variant_id: str, data: bytes) -> Asset:
        if not ID_RE.fullmatch(variant_id):
            raise AssetRegistryError(f"variant id는 kebab-case여야 합니다: {variant_id!r}")
        validated = _validate_asset_png(data)
        with self._lock:
            manifest = self.manifest()
            asset = next((a for a in manifest.assets if a.id == asset_id), None)
            if asset is None:
                raise AssetRegistryError(f"자산이 없습니다: {asset_id!r}")
            if any(v.id == variant_id for v in asset.variants):
                raise AssetRegistryError(f"이미 존재하는 variant입니다: {asset_id}:{variant_id}")
            self._save_file(asset_id, variant_id, validated.image)
            asset.variants.append(
                AssetVariant(id=variant_id, file=f"{asset_id}/{variant_id}.png")
            )
            self._write(manifest)
            return asset

    def set_archived(self, asset_id: str, variant_id: str | None, archived: bool) -> Asset:
        with self._lock:
            manifest = self.manifest()
            asset = next((a for a in manifest.assets if a.id == asset_id), None)
            if asset is None:
                raise AssetRegistryError(f"자산이 없습니다: {asset_id!r}")
            if variant_id is None:
                asset.archived = archived
            else:
                variant = next((v for v in asset.variants if v.id == variant_id), None)
                if variant is None:
                    raise AssetRegistryError(f"variant가 없습니다: {asset_id}:{variant_id}")
                variant.archived = archived
            self._write(manifest)
            return asset

    # ── 내부 ──

    def _save_file(self, asset_id: str, variant_id: str, image: Image.Image) -> None:
        target_dir = self.root / asset_id
        target_dir.mkdir(parents=True, exist_ok=True)
        image.save(target_dir / f"{variant_id}.png")

    def _write(self, manifest: AssetManifest) -> None:
        tmp = self.root / "manifest.yaml.tmp"
        tmp.write_text(
            "# 자산 라이브러리 v1 — 웹 등록분 포함. 삭제 대신 archived(soft delete).\n"
            + yaml.safe_dump(manifest.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        tmp.replace(self.root / "manifest.yaml")
