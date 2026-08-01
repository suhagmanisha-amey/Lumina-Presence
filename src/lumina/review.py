"""The review surface: a browsable HTML page of everything awaiting your call.

The page is read-only by design — a local HTML file cannot write to disk, and
pretending otherwise would mean running a server just to collect two decisions.
Each card carries the exact command to approve or reject it, so the page is for
reading and the terminal is for deciding.
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from .config import CONTENT_DIR, Config
from .models import Draft, Stage
from .store import list_drafts

_STYLE = """
:root {
  color-scheme: light dark;
  --bg: #fbfaf8; --card: #fff; --ink: #1c1b19; --muted: #6b6862;
  --line: #e5e1da; --accent: #9a5b3d;
}
@media (prefers-color-scheme: dark) {
  :root { --bg:#16150f; --card:#1f1e18; --ink:#ece8df; --muted:#9a958a;
          --line:#33312a; --accent:#d79b74; }
}
:root[data-theme="dark"] {
  --bg:#16150f; --card:#1f1e18; --ink:#ece8df; --muted:#9a958a;
  --line:#33312a; --accent:#d79b74;
}
:root[data-theme="light"] {
  --bg:#fbfaf8; --card:#fff; --ink:#1c1b19; --muted:#6b6862;
  --line:#e5e1da; --accent:#9a5b3d;
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 2.5rem 1.25rem 4rem; background: var(--bg); color: var(--ink);
  font: 16px/1.6 Georgia, 'Iowan Old Style', serif;
}
.wrap { max-width: 46rem; margin: 0 auto; }
h1 { font-size: 1.75rem; margin: 0 0 .25rem; letter-spacing: -.01em; }
.sub { color: var(--muted); margin: 0 0 2.5rem; font-size: .95rem; }
article {
  background: var(--card); border: 1px solid var(--line); border-radius: 10px;
  padding: 1.5rem; margin-bottom: 1.5rem;
}
.meta {
  display: flex; flex-wrap: wrap; gap: .5rem 1rem; color: var(--muted);
  font-size: .8rem; text-transform: uppercase; letter-spacing: .06em;
  font-family: ui-monospace, monospace; margin-bottom: 1rem;
}
.caption { white-space: pre-wrap; margin: 0 0 1rem; }
.tags { color: var(--accent); font-size: .9rem; margin: 0 0 1rem; }
.note {
  border-left: 2px solid var(--line); padding-left: .9rem; color: var(--muted);
  font-size: .9rem; margin: 0 0 1rem;
}
.note strong { color: var(--ink); font-weight: 600; }
.sources { font-size: .85rem; margin: 0 0 1rem; padding-left: 1.1rem; }
.sources a { color: var(--accent); overflow-wrap: anywhere; }
pre {
  background: var(--bg); border: 1px solid var(--line); border-radius: 6px;
  padding: .7rem .9rem; margin: 0; overflow-x: auto;
  font: .82rem/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
}
.empty { color: var(--muted); font-style: italic; }
"""


def _card(draft: Draft, config: Config) -> str:
    esc = html.escape
    meta = [f"id {esc(draft.id)}", draft.created_at.strftime("%d %b %Y")]
    if draft.asset_filename:
        meta.append(esc(draft.asset_filename))
    else:
        meta.append("no media")
    if draft.scheduled_for:
        meta.append(f"for {esc(draft.scheduled_for)}")

    parts = [
        "<article>",
        '<div class="meta">' + "".join(f"<span>{m}</span>" for m in meta) + "</div>",
        f'<p class="caption">{esc(draft.full_caption(config.call_to_action))}</p>',
    ]

    if draft.alt_text:
        parts.append(f'<p class="note"><strong>Alt text.</strong> {esc(draft.alt_text)}</p>')
    if draft.rationale:
        parts.append(f'<p class="note"><strong>Why this pairing.</strong> {esc(draft.rationale)}</p>')

    if draft.topic:
        topic = draft.topic
        parts.append(
            f'<p class="note"><strong>{esc(topic.title)}.</strong> '
            f"{esc(topic.angle)} {esc(topic.why_now)}</p>"
        )
        if topic.sources:
            links = "".join(
                f'<li><a href="{esc(url)}" rel="noreferrer noopener">{esc(url)}</a></li>'
                for url in topic.sources
            )
            parts.append(f'<ul class="sources">{links}</ul>')

    parts.append(
        f"<pre>lumina approve {esc(draft.id)}\nlumina reject {esc(draft.id)} "
        '--note "off-brand"</pre>'
    )
    parts.append("</article>")
    return "\n".join(parts)


def render(config: Config, output: Path | None = None) -> Path:
    """Write the review page and return its path."""
    queued = list_drafts(Stage.QUEUE)
    approved = list_drafts(Stage.APPROVED)

    if queued:
        body = "\n".join(_card(draft, config) for draft in queued)
    else:
        body = '<p class="empty">Nothing awaiting review. Run <code>lumina discover</code>.</p>'

    generated = datetime.now().strftime("%d %b %Y, %H:%M")
    sub = (
        f"{len(queued)} awaiting review · {len(approved)} approved and ready · "
        f"generated {html.escape(generated)}"
    )

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Review queue — {html.escape(config.brand_name)}</title>
<style>{_STYLE}</style></head>
<body><div class="wrap">
<h1>Review queue</h1>
<p class="sub">{sub}</p>
{body}
</div></body></html>
"""

    path = output or (CONTENT_DIR / "review.html")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    return path
