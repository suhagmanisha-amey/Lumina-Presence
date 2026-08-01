"""Real publishing via the Instagram Graph API.

Two-step protocol: create a media container, then publish it. Video containers
are processed asynchronously and must be polled until they report FINISHED.

Note the media constraint: the Graph API does not accept file uploads. It
fetches your media itself from a public HTTPS URL, which is why MEDIA_BASE_URL
exists — it must serve the same filenames as assets/library/.
"""

from __future__ import annotations

import time
from urllib.parse import quote

import requests

from ..config import Config
from ..models import Draft
from .base import PublishError, PublishResult

API_VERSION = "v21.0"
BASE = f"https://graph.facebook.com/{API_VERSION}"

REQUEST_TIMEOUT = 30
VIDEO_POLL_SECONDS = 5
VIDEO_POLL_ATTEMPTS = 60  # ~5 minutes


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
            raise PublishError(f"Could not reach the Instagram API: {exc}") from exc
        return self._parse(response)

    def _get(self, path: str, params: dict[str, str]) -> dict:
        try:
            response = requests.get(f"{BASE}/{path}", params=params, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            raise PublishError(f"Could not reach the Instagram API: {exc}") from exc
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
