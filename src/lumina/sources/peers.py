"""Learning from peer accounts in your niche.

Uses Instagram's Business Discovery API, which returns the public profile and
recent posts of any *Business or Creator* account, given its username. This is
the sanctioned route — the same numbers a visitor sees, read through the API
instead of scraped. Personal accounts are not visible and are not attempted.

What this is for: spotting which *angles and formats* are landing in your niche,
so the topic research has a sense of what the audience actually responds to.
What it is not for: copying captions. The analysis prompt is explicit that ideas
must be transferable observations, not rewrites of someone else's post — and
that distinction is worth keeping, both because near-copies read as derivative
and because the captions belong to whoever wrote them.
"""

from __future__ import annotations

import anthropic
import requests
from pydantic import BaseModel, Field

from ..config import Config
from ..models import PeerPost, PeerProfile, Topic
from ..publish.instagram import API_VERSION, REQUEST_TIMEOUT, api_family, scrub

BASE = f"https://graph.facebook.com/{API_VERSION}"
MODEL = "claude-opus-5"
POSTS_PER_PEER = 25


class PeerLookupError(RuntimeError):
    """A peer profile could not be read. Message is shown to the user."""


class _Ideas(BaseModel):
    topics: list[Topic] = Field(default_factory=list)


def fetch_peer(config: Config, username: str) -> PeerProfile:
    """Read one peer's public profile and recent posts."""
    credentials = config.credentials
    if not credentials.can_publish:
        raise PeerLookupError(
            "Peer analysis needs the Instagram credentials — Business Discovery "
            "is called from your own business account. Missing: "
            + ", ".join(credentials.missing_for_publish())
        )

    token = credentials.ig_access_token or ""
    if api_family(token) == "ig_login":
        raise PeerLookupError(
            "Business Discovery isn't available on this account's API. Your "
            "IG_ACCESS_TOKEN is from Instagram API with Instagram Login "
            "(direct Instagram login, no Facebook Page) — that product doesn't "
            "expose Business Discovery at all, on any token. Publishing still "
            "works fine; `lumina peers` specifically needs the older, "
            "Facebook-Page-linked Instagram Graph API instead."
        )

    handle = username.lstrip("@")
    fields = (
        f"business_discovery.username({handle})"
        "{followers_count,media_count,media.limit(" + str(POSTS_PER_PEER) + ")"
        "{caption,like_count,comments_count,media_type,permalink,timestamp}}"
    )

    try:
        response = requests.get(
            f"{BASE}/{credentials.ig_user_id}",
            params={"fields": fields, "access_token": token},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise PeerLookupError(
            f"Could not reach the Instagram API: {scrub(str(exc), token)}"
        ) from exc

    try:
        payload = response.json()
    except ValueError:
        raise PeerLookupError(
            f"Instagram returned a non-JSON response (HTTP {response.status_code})."
        ) from None

    if "error" in payload:
        message = payload["error"].get("message", "no message")
        raise PeerLookupError(
            f"@{handle}: {scrub(message, token)} — Business Discovery only sees "
            "Business and Creator accounts, so a personal account will fail here."
        )

    discovered = payload.get("business_discovery")
    if not discovered:
        raise PeerLookupError(f"@{handle}: Instagram returned no profile data.")

    posts = [
        PeerPost(
            caption=item.get("caption", "") or "",
            like_count=item.get("like_count", 0) or 0,
            comments_count=item.get("comments_count", 0) or 0,
            media_type=item.get("media_type", "") or "",
            permalink=item.get("permalink", "") or "",
            timestamp=item.get("timestamp", "") or "",
        )
        for item in (discovered.get("media", {}).get("data", []) or [])
    ]

    return PeerProfile(
        username=handle,
        followers_count=discovered.get("followers_count", 0) or 0,
        media_count=discovered.get("media_count", 0) or 0,
        posts=posts,
    )


def _analysis_prompt(config: Config, profiles: list[PeerProfile], count: int) -> str:
    blocks = []
    for profile in profiles:
        lines = [
            f"@{profile.username} — {profile.followers_count:,} followers, "
            f"mean engagement {profile.engagement_rate() * 100:.2f}% of followers"
        ]
        for post in profile.top_posts(6):
            caption = " ".join(post.caption.split())[:280]
            lines.append(
                f"  [{post.media_type or 'POST'}, {post.engagement:,} engagements] {caption}"
            )
        blocks.append("\n".join(lines))

    return f"""These are the best-performing recent posts from peer accounts in
{config.brand_name}'s niche, read from Instagram's public Business Discovery API.

<peers>
{chr(10).join(blocks)}
</peers>

About {config.brand_name}:
- What they do: {config.get("brand.what_we_do", "unspecified")}
- Audience: {config.get("brand.audience", "unspecified")}
- Themes they post about: {", ".join(config.get("content.themes", []) or []) or "unspecified"}
- Topics to avoid: {", ".join(config.get("content.avoid", []) or []) or "none"}

Work out what is actually driving engagement here — the recurring formats,
structures, angles, and subjects — then propose the {count} strongest content
opportunities that follow from it for this brand specifically.

Two hard rules:

1. Propose transferable *angles*, not rewrites. "Behind-the-scenes of a process
   the audience never sees" is an angle. Reworded versions of a specific peer's
   caption are not, and are worse than useless — they read as derivative to the
   overlapping audience that follows both accounts.

2. Only propose what this brand can honestly post. If a peer's engagement comes
   from a product, event, or claim this brand does not have, say so rather than
   inventing an equivalent.

For each opportunity:
- `title`: the angle, under 10 words.
- `angle`: one sentence on why it fits this brand and its audience.
- `why_now`: what in the peer data suggests this is landing right now. Cite the
   observation, e.g. "carousels explaining process outperformed by ~3x".
- `sources`: permalinks of the peer posts the observation rests on.

If the peer data is too thin to support {count} genuine observations, return
fewer. Padding this with generic advice defeats the point."""


def analyse(config: Config, profiles: list[PeerProfile], count: int = 5) -> list[Topic]:
    """Turn peer post data into content angles for this brand."""
    if not profiles:
        return []
    if not config.credentials.anthropic_api_key:
        raise PeerLookupError("ANTHROPIC_API_KEY is not set.")

    client = anthropic.Anthropic(api_key=config.credentials.anthropic_api_key)
    response = client.messages.parse(
        model=MODEL,
        max_tokens=12000,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        messages=[
            {"role": "user", "content": _analysis_prompt(config, profiles, count)}
        ],
        output_format=_Ideas,
    )

    parsed = response.parsed_output
    return (parsed.topics if parsed else [])[:count]
