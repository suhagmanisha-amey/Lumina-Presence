"""Finding what's worth posting about right now.

Two passes, on purpose:

1. Research — Claude runs live web searches and writes freeform notes.
2. Structure — a second, search-free call turns those notes into Topic objects.

Splitting them keeps the schema-constrained call away from the search tool's
citation blocks, and makes the research step debuggable on its own (the notes
are returned to the caller and logged).
"""

from __future__ import annotations

import anthropic
from pydantic import BaseModel, Field

from ..config import Config
from ..models import Topic

MODEL = "claude-opus-5"
MAX_SEARCH_ROUNDS = 4


class _TopicList(BaseModel):
    """Structured-output schema for pass 2."""

    topics: list[Topic] = Field(default_factory=list)


def _research_prompt(config: Config, count: int) -> str:
    themes = config.get("content.themes", []) or []
    avoid = config.get("content.avoid", []) or []
    return f"""You are researching Instagram content opportunities for {config.brand_name}.

About the brand:
- What they do: {config.get("brand.what_we_do", "unspecified")}
- Audience: {config.get("brand.audience", "unspecified")}
- Location/market: {config.get("brand.location", "unspecified")}

Themes they post about: {", ".join(themes) if themes else "unspecified"}
Topics to avoid entirely: {", ".join(avoid) if avoid else "none"}

Search the web for what is genuinely current and relevant to this audience right
now — recent news, seasonal moments, shifts in the space, conversations that are
actually happening. Then write notes on the {count + 2} strongest content
opportunities you found.

For each one, cover: what it is, why it matters specifically to this brand's
audience (not to the world in general), why it is timely this week rather than
any week, and the URLs you found it in.

Ground every opportunity in something you actually found in search results. If
the search turns up little of substance, say so plainly and return fewer
opportunities rather than padding with generic evergreen ideas — a thin week is
useful information, an invented trend is not."""


def _structure_prompt(notes: str, count: int) -> str:
    return f"""Here are research notes on Instagram content opportunities:

<notes>
{notes}
</notes>

Convert the {count} strongest opportunities into structured topics.

- `title`: the opportunity in under 10 words.
- `angle`: one sentence on why this brand's audience specifically cares.
- `why_now`: one sentence on the timeliness. Be concrete about the timing.
- `sources`: the URLs from the notes that back this topic. Copy them exactly.
  If the notes give no URL for a topic, leave the list empty — do not guess one.

Use only what is in the notes. Do not add opportunities that aren't there."""


def _client(config: Config) -> anthropic.Anthropic:
    if not config.credentials.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set — copy .env.example to .env and add it."
        )
    return anthropic.Anthropic(api_key=config.credentials.anthropic_api_key)


def research(config: Config, count: int) -> str:
    """Pass 1: live web search, freeform notes out."""
    client = _client(config)
    messages: list[anthropic.types.MessageParam] = [
        {"role": "user", "content": _research_prompt(config, count)}
    ]

    for _ in range(MAX_SEARCH_ROUNDS):
        response = client.messages.create(
            model=MODEL,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 8}],
            messages=messages,
        )

        # The server-side search loop can hit its iteration cap mid-turn. Append
        # the partial assistant turn and re-send; the server resumes on its own.
        if response.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": response.content})
            continue

        return "\n".join(
            block.text for block in response.content if block.type == "text"
        ).strip()

    raise RuntimeError(
        f"Trend research did not finish within {MAX_SEARCH_ROUNDS} search rounds."
    )


def find_topics(config: Config, count: int | None = None) -> tuple[list[Topic], str]:
    """Return (topics, raw research notes).

    The notes come back so the caller can log them — when a draft looks off, the
    research pass is usually where it went wrong, and it's otherwise invisible.
    """
    wanted = count or config.drafts_per_run
    notes = research(config, wanted)
    if not notes:
        return [], ""

    client = _client(config)
    response = client.messages.parse(
        model=MODEL,
        max_tokens=8000,
        thinking={"type": "adaptive"},
        output_config={"effort": "medium"},
        messages=[{"role": "user", "content": _structure_prompt(notes, wanted)}],
        output_format=_TopicList,
    )

    parsed = response.parsed_output
    topics = parsed.topics if parsed else []
    return topics[:wanted], notes
