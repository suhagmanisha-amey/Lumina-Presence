"""Publisher selection.

Credentials decide the backend: complete Instagram config means real posting,
anything less falls back to dry-run. Nothing is ever silently half-posted.
"""

from __future__ import annotations

from ..config import Config
from .base import Publisher, PublishError, PublishResult
from .dryrun import DryRunPublisher
from .instagram import InstagramPublisher

__all__ = [
    "Publisher",
    "PublishError",
    "PublishResult",
    "DryRunPublisher",
    "InstagramPublisher",
    "get_publisher",
]


def get_publisher(config: Config, *, force_dry_run: bool = False) -> Publisher:
    if force_dry_run or not config.credentials.can_publish:
        return DryRunPublisher()
    return InstagramPublisher()
