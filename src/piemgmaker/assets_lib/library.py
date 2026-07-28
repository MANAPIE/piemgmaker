"""자산 라이브러리 로더 — manifest.yaml 파싱·검증, ref 해석."""

import hashlib
from pathlib import Path

import yaml

from piemgmaker.schemas.asset import Asset, AssetManifest, AssetVariant


class AssetLibraryError(ValueError):
    pass


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class AssetLibrary:
    def __init__(self, root: Path):
        self.root = root
        manifest_path = root / "manifest.yaml"
        if not manifest_path.is_file():
            raise AssetLibraryError(f"자산 매니페스트 없음: {manifest_path}")
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        self.manifest = AssetManifest.model_validate(data)
        self._by_id = {a.id: a for a in self.manifest.assets}

    def resolve(self, ref: str) -> tuple[Asset, AssetVariant, Path]:
        """ref = 'asset-id' 또는 'asset-id:variant-id' (미지정 시 첫 활성 variant)."""
        asset_id, _, variant_id = ref.partition(":")
        asset = self._by_id.get(asset_id)
        if asset is None:
            raise AssetLibraryError(f"등록되지 않은 자산: {asset_id!r}")
        if asset.archived:
            raise AssetLibraryError(f"숨김 처리된 자산입니다: {asset_id!r}")
        if variant_id:
            variant = next((v for v in asset.variants if v.id == variant_id), None)
            if variant is None:
                raise AssetLibraryError(f"자산 {asset_id!r}에 variant {variant_id!r} 없음")
            if variant.archived:
                raise AssetLibraryError(f"숨김 처리된 variant입니다: {asset_id}:{variant_id}")
        else:
            variant = next((v for v in asset.variants if not v.archived), None)
            if variant is None:
                raise AssetLibraryError(f"자산 {asset_id!r}의 모든 variant가 숨김 상태입니다")
        path = self.root / variant.file
        if not path.is_file():
            raise AssetLibraryError(f"자산 파일 없음: {path}")
        return asset, variant, path

    def validate_files(self) -> list[str]:
        problems: list[str] = []
        for asset in self.manifest.assets:
            for variant in asset.variants:
                path = self.root / variant.file
                if not path.is_file():
                    problems.append(f"{asset.id}:{variant.id} → 파일 없음: {path}")
        return problems
