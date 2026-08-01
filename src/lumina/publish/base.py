"""The publisher contract.

Everything upstream of this file — discovery, composition, review — is
independent of how a post actually reaches Instagram. Swapping DryRunPublisher
for InstagramPublisher is the only change needed to go live.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..config import Config
from ..models import Draft


class PublishError(RuntimeError):
    """A publish attempt failed. The message is shown to the user verbatim."""


@dataclass(frozen=True)
class PublishResult:
    backend: str
    media_id: str | None
    detail: str = ""


@runtime_checkable
class Publisher(Protocol):
    name: str

    def check(self, config: Config) -> list[str]:
        """Return reasons this publisher can't run. Empty list means ready."""
        ...

    def publish(self, draft: Draft, config: Config) -> PublishResult:
        """Post the draft. Raise PublishError on failure."""
        ...
