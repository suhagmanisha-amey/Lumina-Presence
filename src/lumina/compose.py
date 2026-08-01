"""Turning an asset + a topic into a reviewable draft caption."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import anthropic
from pydantic import BaseModel, Field

from .config import Config
from .models import Asset, Draft, Topic

MODEL = "claude-opus-5"


class _Written(BaseModel):
    """What the caption writer must return."""

    caption: str = Field(description="The caption body. No hashtags, no CTA.")
    hashtags: list[str] = Field(description="Hashtags including the leading '#'.")
    alt_text: str = Field(description="Plain description of the image for screen readers.")
    rationale: str = Field(description="One sentence: why this asset for this topic.")


def _voice_block(config: Config) -> str:
    never = config.get("voice.never", []) or []
    examples = config.get("voice.examples", []) or []

    lines = [
        f"Tone: {config.get('voice.tone', 'unspecified')}",
        f"Brand: {config.brand_name} — {config.get('brand.what_we_do', 'unspecified')}",
        f"Audience: {config.get('brand.audience', 'unspecified')}",
    ]
    if never:
        lines.append("Never do any of these:\n" + "\n".join(f"- {rule}" for rule in never))
    if examples:
        formatted = "\n\n".join(f"<example>\n{text}\n</example>" for text in examples)
        lines.append(
            "Past captions this brand was happy with. Match their register and "
            f"rhythm, not their subject matter:\n\n{formatted}"
        )
    return "\n\n".join(lines)


def _prompt(
    config: Config,
    asset: Asset | None,
    topic: Topic | None,
    recent: list[str],
) -> str:
    sections = [
        f"Write one Instagram caption for {config.brand_name}.",
        _voice_block(config),
    ]

    if topic:
        sources = "\n".join(f"- {url}" for url in topic.sources) or "- (none given)"
        sections.append(
            "The post is about this:\n"
            f"Topic: {topic.title}\n"
            f"Angle: {topic.angle}\n"
            f"Why now: {topic.why_now}\n"
            f"Sources:\n{sources}"
        )

    if asset:
        sections.append(
            "It will be posted with this image or video from the brand's own "
            f"library:\n{asset.describe()}\n\n"
            "You cannot see the file. Write only what the description above "
            "supports — do not invent what is visible in it. If the description "
            "is thin, keep the caption about the topic and let the visual carry "
            "itself."
        )
    else:
        sections.append(
            "No image has been chosen yet. Write a caption that would work "
            "alongside a simple, on-brand visual."
        )

    if recent:
        formatted = "\n".join(f"- {text.splitlines()[0][:120]}" for text in recent)
        sections.append(
            "Recent captions from this account. Do not reuse their openings, "
            f"structure, or angles:\n{formatted}"
        )

    banned = config.banned_hashtags
    sections.append(
        "Requirements:\n"
        f"- Caption body under {config.max_caption_chars} characters. Shorter is better.\n"
        "- Open with something specific. No rhetorical questions, no throat-clearing.\n"
        f"- Exactly {config.hashtag_count} hashtags, relevant and specific — "
        "niche tags outperform broad ones.\n"
        + (f"- Never use these hashtags: {', '.join(banned)}\n" if banned else "")
        + "- Do not put hashtags or a call-to-action in the caption body; they are "
        "appended separately.\n"
        "- Write alt text that describes the visual plainly for someone who "
        "cannot see it.\n"
        "- Every factual claim must trace to the topic above. No invented "
        "statistics, prices, dates, or quotes."
    )

    return "\n\n".join(sections)


def _normalise_hashtags(raw: list[str], config: Config) -> list[str]:
    """Enforce the config's always/banned/count rules on whatever came back."""
    banned = set(config.banned_hashtags)
    seen: set[str] = set()
    tags: list[str] = []

    # Pinned tags go first and are exempt from the count trim below.
    for tag in config.always_hashtags:
        key = tag.lower()
        if key not in seen:
            seen.add(key)
            tags.append(tag)

    for tag in raw:
        tag = tag.strip()
        if not tag:
            continue
        if not tag.startswith("#"):
            tag = "#" + tag.lstrip("#")
        key = tag.lower()
        if key in seen or key in banned:
            continue
        seen.add(key)
        tags.append(tag)

    pinned = len(config.always_hashtags)
    return tags[: max(config.hashtag_count, pinned)]


def write_draft(
    config: Config,
    *,
    asset: Asset | None = None,
    topic: Topic | None = None,
    recent_captions: list[str] | None = None,
    scheduled_for: str | None = None,
) -> Draft:
    """Compose a single draft. Does not save it — the caller decides that."""
    if not config.credentials.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set — copy .env.example to .env and add it."
        )

    client = anthropic.Anthropic(api_key=config.credentials.anthropic_api_key)
    response = client.messages.parse(
        model=MODEL,
        max_tokens=8000,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        messages=[
            {"role": "user", "content": _prompt(config, asset, topic, recent_captions or [])}
        ],
        output_format=_Written,
    )

    written = response.parsed_output
    if written is None:
        raise RuntimeError(
            "The caption writer returned nothing usable "
            f"(stop_reason={response.stop_reason})."
        )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return Draft(
        id=f"{stamp}-{uuid.uuid4().hex[:6]}",
        caption=written.caption.strip(),
        hashtags=_normalise_hashtags(written.hashtags, config),
        alt_text=written.alt_text.strip(),
        rationale=written.rationale.strip(),
        asset_id=asset.id if asset else None,
        asset_filename=asset.filename if asset else None,
        topic=topic,
        scheduled_for=scheduled_for,
    )
