"""The default publisher: prints what would be posted, posts nothing.

This is what runs until Instagram credentials are configured, so the whole
pipeline is exercisable end-to-end before any Meta setup exists.
"""

from __future__ import annotations

import click

from ..config import Config
from ..models import Draft
from .base import PublishResult


class DryRunPublisher:
    name = "dry-run"

    def check(self, config: Config) -> list[str]:
        return []

    def publish(self, draft: Draft, config: Config) -> PublishResult:
        click.echo()
        click.secho(f"  [dry run] would post {draft.id}", fg="cyan", bold=True)
        if draft.asset_filename:
            click.echo(f"  media:    {draft.asset_filename}")
        click.echo(f"  alt text: {draft.alt_text or '(none)'}")
        click.echo("  caption:")
        for line in draft.full_caption(config.call_to_action).splitlines():
            click.echo(f"    {line}")
        click.echo()
        return PublishResult(
            backend=self.name,
            media_id=None,
            detail="Dry run — nothing was sent to Instagram.",
        )
