"""Loading and validating config/brand.yaml plus environment credentials."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

FILL_ME = "FILL_ME"

# Repo root, derived from this file's location: src/lumina/config.py -> ../../
ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "brand.yaml"
ASSET_DIR = ROOT / "assets" / "library"
CONTENT_DIR = ROOT / "content"


class ConfigError(RuntimeError):
    """Raised when config is missing or unusable. Message is shown to the user."""


@dataclass(frozen=True)
class Credentials:
    anthropic_api_key: str | None
    ig_user_id: str | None
    ig_access_token: str | None
    media_base_url: str | None

    @property
    def can_publish(self) -> bool:
        """True only if every piece needed for a real Graph API post is present."""
        return bool(self.ig_user_id and self.ig_access_token and self.media_base_url)

    def missing_for_publish(self) -> list[str]:
        names = {
            "IG_USER_ID": self.ig_user_id,
            "IG_ACCESS_TOKEN": self.ig_access_token,
            "MEDIA_BASE_URL": self.media_base_url,
        }
        return [key for key, value in names.items() if not value]


@dataclass(frozen=True)
class Config:
    raw: dict[str, Any]
    credentials: Credentials
    path: Path = CONFIG_PATH

    # --- convenience accessors, so callers don't index dicts everywhere ---

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.raw
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    @property
    def brand_name(self) -> str:
        return self.get("brand.name", "this brand")

    @property
    def call_to_action(self) -> str:
        return self.get("content.caption.call_to_action", "") or ""

    @property
    def drafts_per_run(self) -> int:
        return int(self.get("schedule.drafts_per_run", 3))

    @property
    def max_caption_chars(self) -> int:
        return int(self.get("content.caption.max_chars", 900))

    @property
    def hashtag_count(self) -> int:
        return int(self.get("content.caption.hashtags.count", 8))

    @property
    def always_hashtags(self) -> list[str]:
        return list(self.get("content.caption.hashtags.always", []) or [])

    @property
    def banned_hashtags(self) -> list[str]:
        return [h.lower() for h in (self.get("content.caption.hashtags.banned", []) or [])]

    @property
    def allow_autopublish(self) -> bool:
        return bool(self.get("safety.allow_autopublish", False))

    @property
    def max_posts_per_day(self) -> int:
        return int(self.get("safety.max_posts_per_day", 2))

    def unfilled_fields(self) -> list[str]:
        """Dotted paths still set to FILL_ME. Non-empty means setup isn't done."""
        found: list[str] = []

        def walk(node: Any, trail: str) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    walk(value, f"{trail}.{key}" if trail else key)
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    walk(value, f"{trail}[{index}]")
            elif isinstance(node, str) and node.strip() == FILL_ME:
                found.append(trail)

        walk(self.raw, "")
        return found


@dataclass
class _Loaded:
    config: Config | None = None
    tried: bool = False
    warnings: list[str] = field(default_factory=list)


_cache = _Loaded()


def load_config(path: Path | None = None, *, reload: bool = False) -> Config:
    """Read brand.yaml and .env. Cached — call with reload=True to re-read."""
    if _cache.config is not None and not reload and path is None:
        return _cache.config

    config_path = path or CONFIG_PATH
    if not config_path.exists():
        raise ConfigError(
            f"No config at {config_path}. Copy config/brand.yaml from the repo "
            "and fill in the FILL_ME fields."
        )

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{config_path} is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"{config_path} must be a YAML mapping at the top level.")

    load_dotenv(ROOT / ".env")
    credentials = Credentials(
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
        ig_user_id=os.environ.get("IG_USER_ID") or None,
        ig_access_token=os.environ.get("IG_ACCESS_TOKEN") or None,
        media_base_url=(os.environ.get("MEDIA_BASE_URL") or "").rstrip("/") or None,
    )

    config = Config(raw=raw, credentials=credentials, path=config_path)
    if path is None:
        _cache.config = config
    return config
