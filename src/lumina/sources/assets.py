"""Reading your existing media library.

Drop files into assets/library/. To tell the caption writer what a file is,
put a sidecar next to it with the same stem:

    assets/library/diwali-popup.jpg
    assets/library/diwali-popup.txt     <- free text description
    assets/library/diwali-popup.json    <- {"caption_hint": "...", "tags": [...]}

A sidecar is optional but it is the single highest-leverage thing you can do
for caption quality — the model cannot see the photo, only what you say about it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..config import ASSET_DIR
from ..models import Asset

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_SUFFIXES = {".mp4", ".mov"}
MEDIA_SUFFIXES = IMAGE_SUFFIXES | VIDEO_SUFFIXES


def _asset_id(path: Path) -> str:
    """Content-addressed so re-runs and renames stay consistent.

    Hashes the first 1 MB plus the total size rather than the whole file —
    enough to distinguish real assets, and fast on a library of large videos.
    """
    digest = hashlib.sha256()
    digest.update(str(path.stat().st_size).encode())
    with path.open("rb") as handle:
        digest.update(handle.read(1_048_576))
    return digest.hexdigest()[:12]


def _read_sidecar(media_path: Path) -> tuple[str, list[str]]:
    """Return (caption_hint, tags) from a .json or .txt sidecar, if present."""
    json_path = media_path.with_suffix(".json")
    if json_path.exists():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return "", []
        if isinstance(data, dict):
            tags = data.get("tags", [])
            return (
                str(data.get("caption_hint", "")).strip(),
                [str(t) for t in tags] if isinstance(tags, list) else [],
            )
        return "", []

    txt_path = media_path.with_suffix(".txt")
    if txt_path.exists():
        return txt_path.read_text(encoding="utf-8").strip(), []

    return "", []


def load_library(
    directory: Path | None = None,
    *,
    exclude_ids: set[str] | None = None,
) -> list[Asset]:
    """Scan the asset directory. Files without a known media suffix are skipped."""
    root = directory or ASSET_DIR
    if not root.exists():
        return []

    exclude = exclude_ids or set()
    assets: list[Asset] = []

    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in MEDIA_SUFFIXES:
            continue

        asset_id = _asset_id(path)
        if asset_id in exclude:
            continue

        caption_hint, tags = _read_sidecar(path)
        assets.append(
            Asset(
                id=asset_id,
                filename=path.name,
                path=str(path),
                media_type="image" if path.suffix.lower() in IMAGE_SUFFIXES else "video",
                size_bytes=path.stat().st_size,
                caption_hint=caption_hint,
                tags=tags,
            )
        )

    return assets
