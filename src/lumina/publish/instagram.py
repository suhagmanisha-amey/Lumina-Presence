"""Real publishing via the Instagram Graph API.

Two-step protocol: create a media container, then publish it. Video containers
are processed asynchronously and must be polled until they report FINISHED.

Note the media constraint: the Graph API does not accept file uploads. It
fetches your media itself from a public HTTPS URL, which is why MEDIA_BASE_URL
exists — it must serve the same filenames as assets/library/.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from urllib.parse import quote, urlparse

import requests

from ..config import Config
from ..models import Draft
from .base import PublishError, PublishResult

API_VERSION = "v21.0"
BASE = f"https://graph.facebook.com/{API_VERSION}"

REQUEST_TIMEOUT = 30
VIDEO_POLL_SECONDS = 5
VIDEO_POLL_ATTEMPTS = 60  # ~5 minutes

# Only these can post via the API. A personal account authenticates fine and
# then fails at publish, which is why this is checked up front.
PUBLISHABLE_ACCOUNT_TYPES = {"BUSINESS", "MEDIA_CREATOR", "CREATOR"}


@dataclass(frozen=True)
class Check:
    """One preflight result. `ok=False` means publishing will fail."""

    label: str
    ok: bool
    detail: str


def scrub(text: str, secret: str | None) -> str:
    """Strip an access token out of text destined for a terminal or a log.

    `requests` puts the full query string in its exception messages, so an
    unscrubbed network error prints the token — and those get pasted into
    issues and chat threads. Redact at every boundary rather than trusting
    callers to remember.
    """
    if secret and len(secret) >= 8:
        text = text.replace(secret, "***REDACTED***")
    # Belt and braces: catch a token that arrived percent-encoded, or one
    # that differs from the value we hold (e.g. after a redirect).
    return re.sub(r"(access_token=)[^&\s)]+", r"\1***REDACTED***", text)


class InstagramPublisher:
    name = "instagram-graph"

    def check(self, config: Config) -> list[str]:
        problems = [
            f"{name} is not set" for name in config.credentials.missing_for_publish()
        ]
        return problems

    # --- internals -------------------------------------------------------

    def _post(self, path: str, params: dict[str, str]) -> dict:
        try:
            response = requests.post(f"{BASE}/{path}", data=params, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            raise PublishError(
                "Could not reach the Instagram API: "
                f"{scrub(str(exc), params.get('access_token'))}"
            ) from exc
        return self._parse(response)

    def _get(self, path: str, params: dict[str, str]) -> dict:
        try:
            response = requests.get(f"{BASE}/{path}", params=params, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            raise PublishError(
                "Could not reach the Instagram API: "
                f"{scrub(str(exc), params.get('access_token'))}"
            ) from exc
        return self._parse(response)

    @staticmethod
    def _parse(response: requests.Response) -> dict:
        try:
            payload = response.json()
        except ValueError:
            raise PublishError(
                f"Instagram returned a non-JSON response (HTTP {response.status_code})."
            ) from None

        if "error" in payload:
            error = payload["error"]
            raise PublishError(
                f"Instagram API error {error.get('code', '?')}: "
                f"{error.get('message', 'no message')}"
                + (f" — {error['error_user_msg']}" if error.get("error_user_msg") else "")
            )
        if not response.ok:
            raise PublishError(f"Instagram API returned HTTP {response.status_code}.")
        return payload

    def _media_url(self, config: Config, filename: str) -> str:
        base = config.credentials.media_base_url
        return f"{base}/{quote(filename)}"

    def _await_container(self, container_id: str, token: str) -> None:
        """Block until an async (video) container finishes processing."""
        for _ in range(VIDEO_POLL_ATTEMPTS):
            payload = self._get(
                container_id, {"fields": "status_code,status", "access_token": token}
            )
            status = payload.get("status_code")
            if status == "FINISHED":
                return
            if status == "ERROR":
                raise PublishError(
                    "Instagram failed to process the video: "
                    f"{payload.get('status', 'no detail given')}"
                )
            time.sleep(VIDEO_POLL_SECONDS)

        raise PublishError(
            "Instagram was still processing the video after "
            f"{VIDEO_POLL_ATTEMPTS * VIDEO_POLL_SECONDS}s. It may still publish on "
            "their side — check the account before retrying."
        )

    # --- preflight -------------------------------------------------------

    def verify(self, config: Config, sample_filename: str | None = None) -> list[Check]:
        """Prove the credentials work *before* a post depends on them.

        Every failure here is one that would otherwise surface mid-publish,
        after a media container had already been created.
        """
        checks: list[Check] = []
        credentials = config.credentials

        missing = credentials.missing_for_publish()
        if missing:
            return [Check("credentials", False, f"not set: {', '.join(missing)}")]
        checks.append(Check("credentials", True, "all four values present"))

        token = credentials.ig_access_token or ""
        user_id = credentials.ig_user_id or ""

        # 1. Does the token authenticate, and does it reach *this* account?
        try:
            account = self._get(
                user_id,
                {"fields": "id,username,account_type", "access_token": token},
            )
        except PublishError as exc:
            return checks + [
                Check(
                    "account",
                    False,
                    f"{exc} — check IG_USER_ID is the Instagram *user* id "
                    "(not the Page id) and that the token has not expired",
                )
            ]

        username = account.get("username", "unknown")
        account_type = (account.get("account_type") or "").upper()
        checks.append(Check("account", True, f"@{username} (id {account.get('id')})"))

        # 2. Personal accounts authenticate fine and then cannot publish.
        if account_type in PUBLISHABLE_ACCOUNT_TYPES:
            checks.append(Check("account type", True, account_type))
        else:
            checks.append(
                Check(
                    "account type",
                    False,
                    f"{account_type or 'unknown'} — must be a Business or Creator "
                    "account to publish via the API",
                )
            )

        # 3. Meta caps API posts per rolling 24h, separately from our own limit.
        try:
            quota = self._get(
                f"{user_id}/content_publishing_limit",
                {"fields": "config,quota_usage", "access_token": token},
            )
            entry = (quota.get("data") or [{}])[0]
            used = entry.get("quota_usage", 0)
            cap = (entry.get("config") or {}).get("quota_total", 50)
            checks.append(Check("meta quota", True, f"{used}/{cap} posts used in last 24h"))
        except PublishError as exc:
            # Informational only — a missing quota endpoint shouldn't block a post.
            checks.append(Check("meta quota", True, f"could not read ({exc})"))

        # 4. The piece most often still missing: publicly fetchable media.
        checks.append(self._check_media(config, sample_filename))
        return checks

    def _check_media(self, config: Config, filename: str | None) -> Check:
        base = config.credentials.media_base_url or ""
        if not urlparse(base).scheme == "https":
            return Check(
                "media hosting",
                False,
                f"MEDIA_BASE_URL is {base!r} — Instagram only fetches over HTTPS",
            )

        if not filename:
            return Check(
                "media hosting",
                True,
                f"{base} is HTTPS (no local asset to test-fetch — add one to "
                "assets/library/ for a full check)",
            )

        url = self._media_url(config, filename)
        try:
            response = requests.head(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            # Not every host implements HEAD; fall back to a 1-byte ranged GET.
            if response.status_code >= 400:
                response = requests.get(
                    url,
                    timeout=REQUEST_TIMEOUT,
                    allow_redirects=True,
                    headers={"Range": "bytes=0-0"},
                    stream=True,
                )
                response.close()
        except requests.RequestException as exc:
            return Check("media hosting", False, f"{url} is not reachable: {exc}")

        if response.status_code >= 400:
            return Check(
                "media hosting",
                False,
                f"{url} returned HTTP {response.status_code} — Instagram fetches "
                "this URL itself, so it must be public and unauthenticated",
            )

        content_type = response.headers.get("content-type", "")
        if not content_type.startswith(("image/", "video/")):
            return Check(
                "media hosting",
                False,
                f"{url} serves {content_type or 'no content-type'} — expected an "
                "image or video type",
            )

        return Check("media hosting", True, f"{url} → {content_type}")

    # --- the contract ----------------------------------------------------

    def publish(self, draft: Draft, config: Config) -> PublishResult:
        problems = self.check(config)
        if problems:
            raise PublishError("; ".join(problems))
        if not draft.asset_filename:
            raise PublishError(
                f"Draft {draft.id} has no media attached. Instagram requires an "
                "image or video — text-only posts are not possible via the API."
            )

        credentials = config.credentials
        token = credentials.ig_access_token or ""
        user_id = credentials.ig_user_id or ""
        media_url = self._media_url(config, draft.asset_filename)
        is_video = draft.asset_filename.lower().endswith((".mp4", ".mov"))

        params: dict[str, str] = {
            "caption": draft.full_caption(config.call_to_action),
            "access_token": token,
        }
        if is_video:
            params["media_type"] = "REELS"
            params["video_url"] = media_url
        else:
            params["image_url"] = media_url
            if draft.alt_text:
                params["alt_text"] = draft.alt_text

        container = self._post(f"{user_id}/media", params)
        container_id = container.get("id")
        if not container_id:
            raise PublishError("Instagram did not return a media container id.")

        if is_video:
            self._await_container(container_id, token)

        published = self._post(
            f"{user_id}/media_publish",
            {"creation_id": container_id, "access_token": token},
        )
        media_id = published.get("id")
        if not media_id:
            raise PublishError("Instagram accepted the container but returned no media id.")

        return PublishResult(
            backend=self.name,
            media_id=media_id,
            detail=f"Published from {media_url}",
        )
