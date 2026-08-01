"""Draft persistence: one JSON file per draft, one directory per stage.

Deliberately not a database. Drafts are few, you want to read and hand-edit
them before approving, and `git diff` on a review queue is genuinely useful.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import CONTENT_DIR
from .models import Draft, Stage


def stage_dir(stage: Stage, root: Path | None = None) -> Path:
    path = (root or CONTENT_DIR) / stage.value
    path.mkdir(parents=True, exist_ok=True)
    return path


def draft_path(draft_id: str, stage: Stage, root: Path | None = None) -> Path:
    return stage_dir(stage, root) / f"{draft_id}.json"


def save(draft: Draft, root: Path | None = None) -> Path:
    """Write the draft to its current stage's directory."""
    path = draft_path(draft.id, draft.stage, root)
    path.write_text(draft.model_dump_json(indent=2), encoding="utf-8")
    return path


def load(path: Path) -> Draft:
    return Draft.model_validate_json(path.read_text(encoding="utf-8"))


def list_drafts(stage: Stage, root: Path | None = None) -> list[Draft]:
    """All drafts in a stage, oldest first."""
    drafts = [load(p) for p in sorted(stage_dir(stage, root).glob("*.json"))]
    drafts.sort(key=lambda d: d.created_at)
    return drafts


def find(draft_id: str, root: Path | None = None) -> tuple[Draft, Path] | None:
    """Locate a draft by id across every stage."""
    for stage in Stage:
        path = draft_path(draft_id, stage, root)
        if path.exists():
            return load(path), path
    return None


def move(draft: Draft, to: Stage, root: Path | None = None) -> Draft:
    """Transition a draft to a new stage, deleting the file at the old one."""
    old_path = draft_path(draft.id, draft.stage, root)
    draft.stage = to
    save(draft, root)
    if old_path.exists() and old_path != draft_path(draft.id, to, root):
        old_path.unlink()
    return draft


def published_in_last_day(root: Path | None = None) -> int:
    """How many posts went out in the past 24h — backs the daily rate limit."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=1)
    count = 0
    for draft in list_drafts(Stage.PUBLISHED, root):
        published = draft.published_at
        if published is None:
            continue
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        if published >= cutoff:
            count += 1
    return count


def recent_captions(limit: int = 12, root: Path | None = None) -> list[str]:
    """Recently published + approved captions, newest first.

    Fed back into the caption writer so it doesn't reuse an opening line or
    re-post the same angle three weeks running.
    """
    drafts = list_drafts(Stage.PUBLISHED, root) + list_drafts(Stage.APPROVED, root)
    drafts.sort(key=lambda d: d.published_at or d.created_at, reverse=True)
    return [d.caption for d in drafts[:limit]]


def used_asset_ids(root: Path | None = None) -> set[str]:
    """Assets already spoken for, so discovery doesn't propose them twice."""
    ids: set[str] = set()
    for stage in (Stage.QUEUE, Stage.APPROVED, Stage.PUBLISHED):
        for draft in list_drafts(stage, root):
            if draft.asset_id:
                ids.add(draft.asset_id)
    return ids


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
