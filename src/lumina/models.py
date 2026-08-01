"""Data shapes shared across the pipeline.

Drafts are stored as JSON on disk (one file per draft) rather than in a
database — they're few, human-readable, and easy to hand-edit before approval,
which matters for a review workflow.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Stage(str, enum.Enum):
    """Where a draft sits in the pipeline. Maps 1:1 to a content/ subdirectory."""

    QUEUE = "queue"
    APPROVED = "approved"
    PUBLISHED = "published"
    REJECTED = "rejected"


class Asset(BaseModel):
    """A photo or video from your library, plus whatever we know about it."""

    id: str
    """Stable, content-derived — the same file always gets the same id."""

    filename: str
    path: str
    media_type: Literal["image", "video"]
    size_bytes: int

    caption_hint: str = ""
    """Free text from a sidecar file, e.g. 'diwali popup, 2025, shot by Ravi'."""

    tags: list[str] = Field(default_factory=list)

    def describe(self) -> str:
        """One line for the caption writer's prompt."""
        parts = [f"{self.filename} ({self.media_type})"]
        if self.caption_hint:
            parts.append(self.caption_hint)
        if self.tags:
            parts.append("tags: " + ", ".join(self.tags))
        return " — ".join(parts)


class Topic(BaseModel):
    """Something worth posting about, found by the trend scan."""

    title: str
    angle: str
    """Why this matters to *this* brand's audience, in one sentence."""

    why_now: str
    sources: list[str] = Field(default_factory=list)
    """URLs backing the claim. A topic with no sources is a red flag."""


class PeerPost(BaseModel):
    """One public post from a peer account, via Instagram Business Discovery."""

    caption: str = ""
    like_count: int = 0
    comments_count: int = 0
    media_type: str = ""
    permalink: str = ""
    timestamp: str = ""

    @property
    def engagement(self) -> int:
        return self.like_count + self.comments_count


class PeerProfile(BaseModel):
    """A peer account and its recent public posts.

    Only public, aggregate data — the same numbers any visitor sees on the
    profile. Business Discovery exposes nothing private.
    """

    username: str
    followers_count: int = 0
    media_count: int = 0
    posts: list[PeerPost] = Field(default_factory=list)

    def engagement_rate(self) -> float:
        """Mean engagement per post as a share of followers."""
        if not self.posts or self.followers_count <= 0:
            return 0.0
        mean = sum(p.engagement for p in self.posts) / len(self.posts)
        return mean / self.followers_count

    def top_posts(self, limit: int = 5) -> list[PeerPost]:
        return sorted(self.posts, key=lambda p: p.engagement, reverse=True)[:limit]


class Draft(BaseModel):
    """A proposed post, awaiting your call."""

    id: str
    created_at: datetime = Field(default_factory=_now)
    stage: Stage = Stage.QUEUE

    caption: str
    hashtags: list[str] = Field(default_factory=list)
    alt_text: str = ""
    """Accessibility description. Instagram exposes this; most accounts skip it."""

    asset_id: str | None = None
    asset_filename: str | None = None
    topic: Topic | None = None

    rationale: str = ""
    """Why the agent paired this asset with this topic. Read this on review."""

    scheduled_for: str | None = None

    # Set once the draft leaves the queue.
    reviewed_at: datetime | None = None
    review_note: str = ""
    published_at: datetime | None = None
    instagram_media_id: str | None = None
    publish_backend: str | None = None

    def full_caption(self, call_to_action: str = "") -> str:
        """Caption as it will actually appear, hashtags and CTA included."""
        blocks = [self.caption.strip()]
        if call_to_action.strip():
            blocks.append(call_to_action.strip())
        if self.hashtags:
            blocks.append(" ".join(self.hashtags))
        return "\n\n".join(blocks)
