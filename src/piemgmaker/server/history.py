"""히스토리 — 디렉토리 스캔 기반 목록·검색 (DB 없음, 소규모 전제)."""

import json
from pathlib import Path

import yaml


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _thumb(job_dir: Path) -> str | None:
    for kind in ("final", "candidates"):
        images = sorted((job_dir / kind).glob("*.png")) if (job_dir / kind).is_dir() else []
        if images:
            return f"{kind}/{images[0].name}"
    return None


def scan_jobs(
    jobs_root: Path,
    q: str | None = None,
    pack: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    entries = []
    if jobs_root.is_dir():
        for job_dir in jobs_root.iterdir():
            if not job_dir.is_dir():
                continue
            status = _read_json(job_dir / "status.json")
            if status is None:
                continue  # 웹 밖(CLI)에서 만든 잡 디렉토리는 status가 없으면 제외
            brief_path = job_dir / "brief.yaml"
            brief = (
                yaml.safe_load(brief_path.read_text(encoding="utf-8"))
                if brief_path.is_file()
                else {}
            ) or {}
            packs = (brief.get("style") or {}).get("style_packs") or []
            haystack = f"{brief.get('campaign_text', '')} {brief.get('object_concept', '')}".lower()
            if q and q.lower() not in haystack:
                continue
            if pack and pack not in packs:
                continue
            entries.append(
                {
                    "job_id": job_dir.name,
                    "created_at": status.get("created_at"),
                    "state": status.get("state"),
                    "passed": status.get("passed"),
                    "campaign_text": brief.get("campaign_text", ""),
                    "object_concept": brief.get("object_concept", ""),
                    "packs": packs,
                    "thumb": _thumb(job_dir),
                    "selected": (job_dir / "selection.json").is_file(),
                }
            )
    entries.sort(key=lambda e: e.get("created_at") or 0, reverse=True)
    total = len(entries)
    start = max(page - 1, 0) * page_size
    return {
        "items": entries[start : start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
