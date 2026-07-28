import numpy as np
import pytest
import yaml
from PIL import Image

from piemgmaker.assets_lib.library import AssetLibrary, AssetLibraryError, file_sha256
from tests.conftest import make_rgba


@pytest.fixture
def library_root(tmp_path):
    (tmp_path / "badge").mkdir()
    Image.fromarray(make_rgba(32, 32, (0, 0, 255, 255)), "RGBA").save(
        tmp_path / "badge" / "main.png"
    )
    (tmp_path / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "assets": [
                    {
                        "id": "badge",
                        "name": "배지",
                        "type": "object",
                        "variants": [
                            {"id": "main", "file": "badge/main.png"},
                            {"id": "ghost", "file": "badge/ghost.png"},  # 파일 없음
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_ref는_variant_생략_시_첫_variant로_해석된다(library_root):
    library = AssetLibrary(library_root)
    asset, variant, path = library.resolve("badge")
    assert (asset.id, variant.id) == ("badge", "main")
    assert path.is_file()
    assert file_sha256(path) == file_sha256(path)  # 결정론


def test_없는_자산과_없는_variant는_명시적으로_실패한다(library_root):
    library = AssetLibrary(library_root)
    with pytest.raises(AssetLibraryError):
        library.resolve("unknown")
    with pytest.raises(AssetLibraryError):
        library.resolve("badge:nope")


def test_파일이_없는_variant는_resolve와_validate에서_걸린다(library_root):
    library = AssetLibrary(library_root)
    with pytest.raises(AssetLibraryError):
        library.resolve("badge:ghost")
    problems = library.validate_files()
    assert len(problems) == 1 and "ghost" in problems[0]


def test_매니페스트가_없으면_로드가_실패한다(tmp_path):
    with pytest.raises(AssetLibraryError):
        AssetLibrary(tmp_path)


def test_리포의_매니페스트는_스키마를_통과하고_파일이_존재한다():
    """실사용 라이브러리 — 비어 있음을 가정하지 않는다 (웹 등록분 포함)."""
    from pathlib import Path

    library = AssetLibrary(Path(__file__).parent.parent / "assets")
    assert library.validate_files() == []  # 등록된 모든 variant 파일 실존
