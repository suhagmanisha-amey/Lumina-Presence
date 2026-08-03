"""Command line entry point.

    lumina doctor      check config and credentials are present
    lumina verify      call Meta and prove those credentials work
    lumina discover    research topics, pair assets, write drafts
    lumina review      render and open the review page
    lumina approve ID  move a draft to the approved queue
    lumina reject ID   discard a draft
    lumina publish     post approved drafts
    lumina status      counts by stage
"""

from __future__ import annotations

import sys
import webbrowser
from datetime import datetime, timezone

import click

from . import review as review_module
from .compose import write_draft
from .config import ASSET_DIR, ConfigError, load_config
from .models import Draft, Stage
from .publish import InstagramPublisher, PublishError, get_publisher
from .sources import find_topics, load_library
from .sources.peers import PeerLookupError, analyse, fetch_peer
from .store import (
    find,
    list_drafts,
    load_peer_ideas,
    move,
    published_in_last_day,
    recent_captions,
    save,
    save_peer_ideas,
    used_asset_ids,
)


def _load():
    try:
        return load_config()
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc


def _summarise(draft: Draft) -> str:
    head = draft.caption.splitlines()[0] if draft.caption else "(empty)"
    return head[:70] + ("…" if len(head) > 70 else "")


@click.group()
@click.version_option(package_name="lumina-presence", prog_name="lumina")
def cli() -> None:
    """Find content, draft posts, review them, publish to Instagram."""


# --------------------------------------------------------------------------


@cli.command()
def doctor() -> None:
    """Check that config and credentials are ready."""
    config = _load()
    problems: list[str] = []

    click.secho(f"config   {config.path}", fg="cyan")

    unfilled = config.unfilled_fields()
    if unfilled:
        problems.append(f"{len(unfilled)} field(s) still set to FILL_ME")
        click.secho("  ✗ unfilled fields:", fg="red")
        for field_path in unfilled:
            click.echo(f"      {field_path}")
    else:
        click.secho("  ✓ brand profile complete", fg="green")

    if config.credentials.anthropic_api_key:
        click.secho("  ✓ ANTHROPIC_API_KEY set", fg="green")
    else:
        problems.append("ANTHROPIC_API_KEY is not set")
        click.secho("  ✗ ANTHROPIC_API_KEY is not set", fg="red")

    assets = load_library()
    if assets:
        described = sum(1 for a in assets if a.caption_hint or a.tags)
        click.secho(f"  ✓ {len(assets)} asset(s) in {ASSET_DIR}", fg="green")
        if described < len(assets):
            click.secho(
                f"      {len(assets) - described} have no sidecar description — "
                "captions for those will be weaker",
                fg="yellow",
            )
    else:
        click.secho(f"  · no assets in {ASSET_DIR} (drafts will be caption-only)", fg="yellow")

    missing = config.credentials.missing_for_publish()
    if missing:
        click.secho(
            f"  · Instagram publishing off — missing {', '.join(missing)}. "
            "Drafts will dry-run.",
            fg="yellow",
        )
    else:
        click.secho(
            "  ✓ Instagram credentials present — run `lumina verify` to confirm "
            "they work",
            fg="green",
        )

    click.echo()
    if problems:
        raise click.ClickException("Not ready: " + "; ".join(problems))
    click.secho("Ready.", fg="green", bold=True)


# --------------------------------------------------------------------------


@cli.command()
@click.option("--count", type=int, default=None, help="How many drafts to produce.")
@click.option("--no-assets", is_flag=True, help="Write caption-only drafts.")
@click.option(
    "--from-peers",
    is_flag=True,
    help="Draft from saved peer angles (run `lumina peers` first) instead of web research.",
)
def discover(count: int | None, no_assets: bool, from_peers: bool) -> None:
    """Research what's current, pair it with your assets, write drafts."""
    config = _load()

    unfilled = config.unfilled_fields()
    if unfilled:
        raise click.ClickException(
            f"{len(unfilled)} config field(s) still say FILL_ME "
            f"(first: {unfilled[0]}). Run `lumina doctor`."
        )

    wanted = count or config.drafts_per_run
    notes = ""

    if from_peers:
        topics = load_peer_ideas()
        if not topics:
            raise click.ClickException(
                "No saved peer angles. Run `lumina peers` first."
            )
        click.secho(f"Using {len(topics)} saved peer angle(s).", fg="cyan")
        topics = topics[:wanted]
    else:
        click.secho("Researching what's current…", fg="cyan")
        try:
            topics, notes = find_topics(config, wanted)
        except Exception as exc:  # noqa: BLE001 — surfaced, not swallowed
            raise click.ClickException(f"Trend research failed: {exc}") from exc

    if not topics:
        click.secho(
            "No topics worth posting about came back. That is a real signal — "
            "either the search turned up little, or your themes are too narrow. "
            "Nothing was drafted.",
            fg="yellow",
        )
        return

    click.secho(f"  found {len(topics)} topic(s)", fg="green")
    for topic in topics:
        click.echo(f"    · {topic.title}")

    assets = [] if no_assets else load_library(exclude_ids=used_asset_ids())
    if not no_assets and not assets:
        click.secho(
            "  no unused assets available — drafting captions without media",
            fg="yellow",
        )

    recent = recent_captions()
    click.echo()
    click.secho("Writing captions…", fg="cyan")

    written: list[Draft] = []
    for index, topic in enumerate(topics):
        asset = assets[index] if index < len(assets) else None
        try:
            draft = write_draft(
                config,
                asset=asset,
                topic=topic,
                recent_captions=recent + [d.caption for d in written],
            )
        except Exception as exc:  # noqa: BLE001
            click.secho(f"  ✗ {topic.title}: {exc}", fg="red")
            continue

        save(draft)
        written.append(draft)
        media = draft.asset_filename or "no media"
        click.secho(f"  ✓ {draft.id}  {media}", fg="green")
        click.echo(f"      {_summarise(draft)}")

    if not written:
        raise click.ClickException("Every caption attempt failed. Nothing was saved.")

    if notes:
        from .config import CONTENT_DIR

        log = CONTENT_DIR / "last-research.md"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(notes, encoding="utf-8")

    click.echo()
    click.secho(
        f"{len(written)} draft(s) waiting. Run `lumina review` to look them over.",
        bold=True,
    )


# --------------------------------------------------------------------------


@cli.command()
@click.option("--count", type=int, default=5, help="How many angles to derive.")
def peers(count: int) -> None:
    """Read peer accounts in your niche and derive content angles.

    Uses Instagram's Business Discovery API — public profile data only, and
    only for Business/Creator accounts. List the handles under
    `content.peers` in config/brand.yaml.
    """
    config = _load()

    handles = config.peers
    if not handles:
        raise click.ClickException(
            "No peers configured. Add handles under `content.peers` in "
            "config/brand.yaml — pick accounts your audience also follows, "
            "not the biggest names in the category."
        )

    click.secho(f"Reading {len(handles)} peer account(s)…", fg="cyan")
    profiles = []
    for handle in handles:
        try:
            profile = fetch_peer(config, handle)
        except PeerLookupError as exc:
            click.secho(f"  ✗ {exc}", fg="red")
            continue

        profiles.append(profile)
        click.secho(
            f"  ✓ @{profile.username}  {profile.followers_count:,} followers, "
            f"{len(profile.posts)} recent posts, "
            f"{profile.engagement_rate() * 100:.2f}% mean engagement",
            fg="green",
        )

    if not profiles:
        raise click.ClickException(
            "No peer profiles could be read. Business Discovery only sees "
            "Business and Creator accounts — check the handles are spelled "
            "correctly and are not personal accounts."
        )

    click.echo()
    click.secho("Working out what's landing…", fg="cyan")
    try:
        topics = analyse(config, profiles, count)
    except Exception as exc:  # noqa: BLE001 — surfaced, not swallowed
        raise click.ClickException(f"Peer analysis failed: {exc}") from exc

    if not topics:
        click.secho(
            "The peer data was too thin to support real observations. Nothing "
            "saved — add more peers, or ones that post more often.",
            fg="yellow",
        )
        return

    for topic in topics:
        click.echo()
        click.secho(f"  {topic.title}", bold=True)
        click.echo(f"    {topic.angle}")
        click.echo(f"    why now: {topic.why_now}")

    path = save_peer_ideas(topics)
    click.echo()
    click.secho(f"Saved {len(topics)} angle(s) to {path}", fg="green")
    click.echo("Draft from them with:  lumina discover --from-peers")


@cli.command()
@click.option("--open/--no-open", "open_browser", default=True, help="Open in a browser.")
def review(open_browser: bool) -> None:
    """Render the review page and list what's waiting."""
    config = _load()
    queued = list_drafts(Stage.QUEUE)

    if not queued:
        click.secho("Nothing awaiting review.", fg="yellow")
    for draft in queued:
        click.echo()
        click.secho(f"{draft.id}  ({draft.asset_filename or 'no media'})", bold=True)
        click.echo(f"  {_summarise(draft)}")
        if draft.topic:
            click.echo(f"  topic: {draft.topic.title}")

    path = review_module.render(config)
    click.echo()
    click.secho(f"Review page: {path}", fg="cyan")
    if open_browser and queued:
        webbrowser.open(path.as_uri())


@cli.command()
@click.argument("draft_id")
@click.option("--note", default="", help="Why you approved it.")
def approve(draft_id: str, note: str) -> None:
    """Approve a queued draft, making it eligible to publish."""
    _load()
    found = find(draft_id)
    if not found:
        raise click.ClickException(f"No draft with id {draft_id}.")

    draft, _ = found
    if draft.stage is not Stage.QUEUE:
        raise click.ClickException(f"{draft_id} is already {draft.stage.value}.")

    draft.reviewed_at = datetime.now(timezone.utc)
    draft.review_note = note
    move(draft, Stage.APPROVED)
    click.secho(f"✓ approved {draft_id}", fg="green")


@cli.command()
@click.argument("draft_id")
@click.option("--note", default="", help="Why you rejected it — improves nothing today, but you'll want the record.")
def reject(draft_id: str, note: str) -> None:
    """Reject a draft. The file is kept in content/rejected/."""
    _load()
    found = find(draft_id)
    if not found:
        raise click.ClickException(f"No draft with id {draft_id}.")

    draft, _ = found
    if draft.stage is Stage.PUBLISHED:
        raise click.ClickException(f"{draft_id} is already published — too late.")

    draft.reviewed_at = datetime.now(timezone.utc)
    draft.review_note = note
    move(draft, Stage.REJECTED)
    click.secho(f"✓ rejected {draft_id}", fg="yellow")


# --------------------------------------------------------------------------


@cli.command()
def verify() -> None:
    """Prove the Instagram credentials actually work, before relying on them.

    `doctor` checks the values exist; this one calls Meta and confirms the token
    authenticates, the account can publish, and your media is fetchable.
    """
    config = _load()

    if not config.credentials.can_publish:
        missing = ", ".join(config.credentials.missing_for_publish())
        raise click.ClickException(
            f"Nothing to verify — still in dry-run mode (missing {missing}). "
            "Add them to .env first."
        )

    publisher = InstagramPublisher()
    sample = next((a.filename for a in load_library()), None)

    click.secho("Checking against the Instagram Graph API…", fg="cyan")
    click.echo()

    checks = publisher.verify(config, sample)
    for check in checks:
        mark, colour = ("✓", "green") if check.ok else ("✗", "red")
        click.secho(f"  {mark} {check.label:<14} {check.detail}", fg=colour)

    click.echo()
    failed = [c for c in checks if not c.ok]
    if failed:
        raise click.ClickException(
            f"{len(failed)} check(s) failed — publishing would fail too. "
            "Fix these before running `lumina publish`."
        )
    click.secho("Verified. Publishing is live.", fg="green", bold=True)


@cli.command()
@click.argument("draft_id", required=False)
@click.option("--all", "publish_all", is_flag=True, help="Publish every approved draft.")
@click.option("--dry-run", is_flag=True, help="Force dry-run even with credentials set.")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
def publish(draft_id: str | None, publish_all: bool, dry_run: bool, yes: bool) -> None:
    """Publish approved drafts to Instagram."""
    config = _load()

    if draft_id:
        found = find(draft_id)
        if not found:
            raise click.ClickException(f"No draft with id {draft_id}.")
        draft, _ = found
        if draft.stage is not Stage.APPROVED:
            raise click.ClickException(
                f"{draft_id} is {draft.stage.value}, not approved. "
                "Run `lumina approve` first."
            )
        targets = [draft]
    elif publish_all:
        targets = list_drafts(Stage.APPROVED)
    else:
        raise click.ClickException("Give a draft id, or pass --all.")

    if not targets:
        click.secho("Nothing approved and waiting.", fg="yellow")
        return

    publisher = get_publisher(config, force_dry_run=dry_run)
    problems = publisher.check(config)
    if problems:
        raise click.ClickException("Publisher not ready: " + "; ".join(problems))

    live = publisher.name != "dry-run"

    # The daily cap is a backstop against a runaway loop or a bad --all, so it
    # binds regardless of how many drafts the caller asked for.
    if live:
        already = published_in_last_day()
        remaining = config.max_posts_per_day - already
        if remaining <= 0:
            raise click.ClickException(
                f"Daily limit reached ({already}/{config.max_posts_per_day} in the "
                "last 24h). Raise safety.max_posts_per_day to change this."
            )
        if len(targets) > remaining:
            click.secho(
                f"Daily limit allows {remaining} more post(s); "
                f"publishing the {remaining} oldest.",
                fg="yellow",
            )
            targets = targets[:remaining]

    if live and not yes:
        click.secho(f"About to post {len(targets)} item(s) to Instagram.", fg="yellow", bold=True)
        for draft in targets:
            click.echo(f"  {draft.id}  {_summarise(draft)}")
        click.confirm("This is public and hard to undo. Continue?", abort=True)

    failures = 0
    for draft in targets:
        try:
            result = publisher.publish(draft, config)
        except PublishError as exc:
            failures += 1
            click.secho(f"✗ {draft.id}: {exc}", fg="red")
            continue

        if live:
            draft.published_at = datetime.now(timezone.utc)
            draft.instagram_media_id = result.media_id
            draft.publish_backend = result.backend
            move(draft, Stage.PUBLISHED)
            click.secho(f"✓ published {draft.id} (media {result.media_id})", fg="green")

    if failures:
        raise click.ClickException(f"{failures} of {len(targets)} failed to publish.")

    if not live:
        click.secho(
            "Dry run — nothing was posted and no draft changed stage.", fg="cyan"
        )


@cli.command()
def status() -> None:
    """Show how many drafts sit at each stage."""
    _load()
    for stage in Stage:
        drafts = list_drafts(stage)
        click.echo(f"{stage.value:<10} {len(drafts)}")
    click.echo()
    click.echo(f"published in last 24h: {published_in_last_day()}")


def main() -> None:
    try:
        cli()
    except KeyboardInterrupt:
        click.echo()
        sys.exit(130)


if __name__ == "__main__":
    main()
