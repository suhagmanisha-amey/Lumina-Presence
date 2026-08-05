"""Tests for the parts that run without touching the network."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from lumina.compose import _normalise_hashtags
from lumina.config import Config, Credentials, load_config
from lumina.models import Draft, Stage
from lumina.publish import DryRunPublisher, InstagramPublisher, get_publisher
from lumina.sources.assets import load_library
from lumina.store import find, list_drafts, move, published_in_last_day, save


def make_config(**overrides) -> Config:
    raw = {
        "brand": {"name": "Test Brand"},
        "content": {
            "caption": {
                "max_chars": 900,
                "call_to_action": "",
                "hashtags": {
                    "count": 3,
                    "always": ["#Pinned"],
                    "banned": ["#followforfollow"],
                },
            }
        },
        "safety": {"max_posts_per_day": 2},
    }
    raw.update(overrides)
    return Config(
        raw=raw,
        credentials=Credentials(
            anthropic_api_key="test", ig_user_id=None, ig_access_token=None,
            media_base_url=None,
        ),
    )


def make_draft(draft_id: str = "d1", **kwargs) -> Draft:
    return Draft(id=draft_id, caption=kwargs.pop("caption", "Hello."), **kwargs)


# --- hashtag normalisation -------------------------------------------------


def test_pinned_hashtag_is_always_present_and_first():
    tags = _normalise_hashtags(["#alpha", "#beta"], make_config())
    assert tags[0] == "#Pinned"
    assert "#alpha" in tags


def test_banned_hashtags_are_dropped():
    tags = _normalise_hashtags(["#followforfollow", "#good"], make_config())
    assert "#followforfollow" not in [t.lower() for t in tags]
    assert "#good" in tags


def test_missing_hash_prefix_is_added_and_duplicates_collapse():
    tags = _normalise_hashtags(["alpha", "#alpha", "#ALPHA"], make_config())
    assert tags.count("#alpha") == 1


def test_count_never_trims_away_a_pinned_tag():
    config = make_config()
    config.raw["content"]["caption"]["hashtags"]["count"] = 1
    tags = _normalise_hashtags(["#a", "#b", "#c"], config)
    assert "#Pinned" in tags


# --- caption assembly ------------------------------------------------------


def test_full_caption_appends_cta_then_hashtags():
    draft = make_draft(caption="Body.", hashtags=["#one", "#two"])
    out = draft.full_caption("Link in bio.")
    assert out.index("Body.") < out.index("Link in bio.") < out.index("#one")


def test_full_caption_omits_empty_cta():
    draft = make_draft(caption="Body.", hashtags=["#one"])
    assert draft.full_caption("") == "Body.\n\n#one"


# --- store round-trips -----------------------------------------------------


def test_save_and_find_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr("lumina.store.CONTENT_DIR", tmp_path)
    draft = make_draft("abc123", caption="Stored.")
    save(draft, tmp_path)

    found = find("abc123", tmp_path)
    assert found is not None
    assert found[0].caption == "Stored."


def test_move_deletes_the_file_at_the_old_stage(tmp_path):
    draft = make_draft("mv1")
    save(draft, tmp_path)
    move(draft, Stage.APPROVED, tmp_path)

    assert list_drafts(Stage.QUEUE, tmp_path) == []
    assert [d.id for d in list_drafts(Stage.APPROVED, tmp_path)] == ["mv1"]


def test_daily_count_only_includes_the_last_24_hours(tmp_path):
    now = datetime.now(timezone.utc)
    for index, when in enumerate([now, now - timedelta(hours=30)]):
        draft = make_draft(f"p{index}", stage=Stage.PUBLISHED, published_at=when)
        save(draft, tmp_path)

    assert published_in_last_day(tmp_path) == 1


# --- asset library ---------------------------------------------------------


def test_library_reads_json_sidecar(tmp_path):
    (tmp_path / "shot.jpg").write_bytes(b"fake-image-bytes")
    (tmp_path / "shot.json").write_text(
        json.dumps({"caption_hint": "the popup", "tags": ["diwali"]})
    )

    [asset] = load_library(tmp_path)
    assert asset.caption_hint == "the popup"
    assert asset.tags == ["diwali"]
    assert asset.media_type == "image"


def test_library_reads_txt_sidecar_and_skips_non_media(tmp_path):
    (tmp_path / "clip.mp4").write_bytes(b"fake-video")
    (tmp_path / "clip.txt").write_text("behind the scenes")
    (tmp_path / "notes.md").write_text("not media")

    [asset] = load_library(tmp_path)
    assert asset.media_type == "video"
    assert asset.caption_hint == "behind the scenes"


def test_asset_ids_are_stable_and_content_derived(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"same-bytes")
    first = load_library(tmp_path)[0].id
    second = load_library(tmp_path)[0].id
    assert first == second


def test_excluded_assets_are_not_returned(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"bytes")
    asset_id = load_library(tmp_path)[0].id
    assert load_library(tmp_path, exclude_ids={asset_id}) == []


# --- publisher selection ---------------------------------------------------


def test_incomplete_credentials_fall_back_to_dry_run():
    assert isinstance(get_publisher(make_config()), DryRunPublisher)


def test_complete_credentials_select_the_instagram_publisher():
    config = Config(
        raw=make_config().raw,
        credentials=Credentials(
            anthropic_api_key="k",
            ig_user_id="1",
            ig_access_token="t",
            media_base_url="https://cdn.example.com",
        ),
    )
    assert isinstance(get_publisher(config), InstagramPublisher)
    assert isinstance(get_publisher(config, force_dry_run=True), DryRunPublisher)


def test_instagram_publisher_reports_each_missing_credential():
    problems = InstagramPublisher().check(make_config())
    assert len(problems) == 3


# --- dual Instagram API family (Graph API vs. Instagram Login) ------------


def test_api_family_detects_instagram_login_token():
    from lumina.publish.instagram import api_family

    assert api_family("IGAAM2ZASrdrElBZ...") == "ig_login"


def test_api_family_defaults_to_graph_api():
    from lumina.publish.instagram import api_family

    assert api_family("EAAGsomeclassictoken") == "graph_api"
    assert api_family("") == "graph_api"


def test_base_for_token_routes_to_the_right_host():
    from lumina.publish.instagram import base_for_token

    assert "graph.instagram.com" in base_for_token("IGAAxyz")
    assert "graph.facebook.com" in base_for_token("EAAGxyz")


def test_requests_are_routed_by_token_not_a_fixed_host(monkeypatch):
    seen_urls = []

    class FakeResponse:
        status_code = 200
        ok = True

        @staticmethod
        def json():
            return {"id": "1", "username": "x", "account_type": "BUSINESS"}

    def fake_get(url, params=None, timeout=None):
        seen_urls.append(url)
        return FakeResponse()

    monkeypatch.setattr("lumina.publish.instagram.requests.get", fake_get)

    publisher = InstagramPublisher()
    publisher._get("me", {"access_token": "IGAAtoken", "fields": "id"})
    publisher._get("me", {"access_token": "EAAGtoken", "fields": "id"})

    assert "graph.instagram.com" in seen_urls[0]
    assert "graph.facebook.com" in seen_urls[1]


def test_verify_flags_instagram_login_tokens_and_the_peers_limitation(monkeypatch):
    stub_graph(
        monkeypatch,
        account={"id": "1", "username": "lumina", "account_type": "BUSINESS"},
        quota={"data": [{"quota_usage": 0, "config": {"quota_total": 50}}]},
    )
    stub_media(monkeypatch)

    config = Config(
        raw=make_config().raw,
        credentials=Credentials(
            anthropic_api_key="k", ig_user_id="1", ig_access_token="IGAAfaketoken",
            media_base_url="https://cdn.example.com",
        ),
    )
    checks = InstagramPublisher().verify(config, "a.jpg")
    family_check = next(c for c in checks if c.label == "API family")
    assert family_check.ok is True
    assert "peers" in family_check.detail.lower()


def test_peers_refuses_an_instagram_login_token_with_a_clear_reason():
    from lumina.sources.peers import PeerLookupError, fetch_peer

    config = Config(
        raw=make_config().raw,
        credentials=Credentials(
            anthropic_api_key="k", ig_user_id="1", ig_access_token="IGAAfaketoken",
            media_base_url="https://cdn.example.com",
        ),
    )
    with pytest.raises(PeerLookupError, match="Business Discovery isn't available"):
        fetch_peer(config, "somepeer")


# --- preflight verification ------------------------------------------------


def live_config(media_base_url: str = "https://cdn.example.com") -> Config:
    return Config(
        raw=make_config().raw,
        credentials=Credentials(
            anthropic_api_key="k",
            ig_user_id="17841400000000000",
            ig_access_token="tok",
            media_base_url=media_base_url,
        ),
    )


def results(checks) -> dict[str, bool]:
    return {c.label: c.ok for c in checks}


def stub_graph(monkeypatch, *, account: dict, quota: dict | None = None):
    """Route the publisher's Graph API reads to canned payloads."""

    def fake_get(self, path, params):
        if path.endswith("content_publishing_limit"):
            if quota is None:
                from lumina.publish.base import PublishError as _E

                raise _E("no quota endpoint")
            return quota
        return account

    monkeypatch.setattr(InstagramPublisher, "_get", fake_get)


def stub_media(monkeypatch, *, status: int = 200, content_type: str = "image/jpeg"):
    class FakeResponse:
        status_code = status
        headers = {"content-type": content_type}

        def close(self):
            pass

    monkeypatch.setattr("lumina.publish.instagram.requests.head", lambda *a, **k: FakeResponse())
    monkeypatch.setattr("lumina.publish.instagram.requests.get", lambda *a, **k: FakeResponse())


def test_verify_passes_for_a_healthy_business_account(monkeypatch):
    stub_graph(
        monkeypatch,
        account={"id": "178414", "username": "lumina", "account_type": "BUSINESS"},
        quota={"data": [{"quota_usage": 3, "config": {"quota_total": 50}}]},
    )
    stub_media(monkeypatch)

    checks = InstagramPublisher().verify(live_config(), "shot.jpg")
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]


def test_verify_flags_a_personal_account(monkeypatch):
    stub_graph(
        monkeypatch,
        account={"id": "1", "username": "someone", "account_type": "PERSONAL"},
    )
    stub_media(monkeypatch)

    checks = InstagramPublisher().verify(live_config(), "shot.jpg")
    assert results(checks)["account type"] is False


def test_verify_flags_a_bad_token_and_stops_early(monkeypatch):
    from lumina.publish.base import PublishError as _E

    def raise_auth(self, path, params):
        raise _E("Instagram API error 190: invalid token")

    monkeypatch.setattr(InstagramPublisher, "_get", raise_auth)

    checks = InstagramPublisher().verify(live_config(), "shot.jpg")
    assert results(checks)["account"] is False
    # No point checking media hosting once the token is dead.
    assert "media hosting" not in results(checks)


def test_verify_rejects_non_https_media_base_url(monkeypatch):
    stub_graph(
        monkeypatch,
        account={"id": "1", "username": "lumina", "account_type": "BUSINESS"},
    )
    checks = InstagramPublisher().verify(live_config("http://cdn.example.com"), "a.jpg")
    assert results(checks)["media hosting"] is False


def test_verify_flags_unreachable_media(monkeypatch):
    stub_graph(
        monkeypatch,
        account={"id": "1", "username": "lumina", "account_type": "BUSINESS"},
    )
    stub_media(monkeypatch, status=404)

    checks = InstagramPublisher().verify(live_config(), "missing.jpg")
    assert results(checks)["media hosting"] is False


def test_verify_flags_media_served_as_html(monkeypatch):
    # A private bucket or an SPA fallback commonly returns a 200 HTML page,
    # which Instagram cannot use as media.
    stub_graph(
        monkeypatch,
        account={"id": "1", "username": "lumina", "account_type": "BUSINESS"},
    )
    stub_media(monkeypatch, content_type="text/html")

    checks = InstagramPublisher().verify(live_config(), "a.jpg")
    assert results(checks)["media hosting"] is False


# --- token redaction -------------------------------------------------------


def test_scrub_removes_a_known_token():
    from lumina.publish.instagram import scrub

    assert "EAAGsecret123" not in scrub("failed for EAAGsecret123", "EAAGsecret123")


def test_scrub_removes_a_token_from_a_url_query_string():
    from lumina.publish.instagram import scrub

    text = "GET /v21.0/1?fields=id&access_token=EAAGabc123 (Caused by ProxyError)"
    cleaned = scrub(text, None)
    assert "EAAGabc123" not in cleaned
    # Surrounding context is preserved so the error is still diagnosable.
    assert "ProxyError" in cleaned and "fields=id" in cleaned


def test_network_errors_never_carry_the_token(monkeypatch):
    import requests as _requests

    def boom(*args, **kwargs):
        raise _requests.RequestException(
            "HTTPSConnectionPool: /v21.0/1?access_token=EAAGsupersecret failed"
        )

    monkeypatch.setattr("lumina.publish.instagram.requests.get", boom)
    checks = InstagramPublisher().verify(live_config(), "a.jpg")

    blob = " ".join(c.detail for c in checks)
    assert "EAAGsupersecret" not in blob
    assert "REDACTED" in blob


def test_verify_without_credentials_reports_only_that(monkeypatch):
    checks = InstagramPublisher().verify(make_config())
    assert results(checks) == {"credentials": False}


def test_unreadable_quota_does_not_fail_verification(monkeypatch):
    stub_graph(
        monkeypatch,
        account={"id": "1", "username": "lumina", "account_type": "BUSINESS"},
        quota=None,  # endpoint raises
    )
    stub_media(monkeypatch)

    checks = InstagramPublisher().verify(live_config(), "a.jpg")
    assert results(checks)["meta quota"] is True
    assert all(c.ok for c in checks)


# --- peer analysis ---------------------------------------------------------


def stub_discovery(monkeypatch, payload: dict):
    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return payload

    monkeypatch.setattr("lumina.sources.peers.requests.get", lambda *a, **k: FakeResponse())


DISCOVERY_OK = {
    "business_discovery": {
        "followers_count": 10_000,
        "media_count": 240,
        "media": {
            "data": [
                {
                    "caption": "Behind the scenes of a repot.",
                    "like_count": 400,
                    "comments_count": 100,
                    "media_type": "CAROUSEL_ALBUM",
                    "permalink": "https://instagram.com/p/aaa",
                    "timestamp": "2026-07-01T10:00:00+0000",
                },
                {
                    "caption": "A quiet morning.",
                    "like_count": 100,
                    "comments_count": 0,
                    "media_type": "IMAGE",
                    "permalink": "https://instagram.com/p/bbb",
                    "timestamp": "2026-07-02T10:00:00+0000",
                },
            ]
        },
    }
}


def test_fetch_peer_parses_a_discovery_payload(monkeypatch):
    from lumina.sources.peers import fetch_peer

    stub_discovery(monkeypatch, DISCOVERY_OK)
    profile = fetch_peer(live_config(), "@somepeer")

    assert profile.username == "somepeer"  # leading @ stripped
    assert profile.followers_count == 10_000
    assert len(profile.posts) == 2


def test_engagement_rate_averages_across_posts(monkeypatch):
    from lumina.sources.peers import fetch_peer

    stub_discovery(monkeypatch, DISCOVERY_OK)
    profile = fetch_peer(live_config(), "somepeer")
    # (500 + 100) / 2 posts = 300 mean, over 10_000 followers.
    assert profile.engagement_rate() == pytest.approx(0.03)


def test_top_posts_are_ranked_by_total_engagement(monkeypatch):
    from lumina.sources.peers import fetch_peer

    stub_discovery(monkeypatch, DISCOVERY_OK)
    top = fetch_peer(live_config(), "somepeer").top_posts(1)
    assert top[0].permalink.endswith("aaa")


def test_engagement_rate_is_zero_when_follower_count_is_unavailable():
    from lumina.models import PeerPost, PeerProfile

    profile = PeerProfile(
        username="x", followers_count=0, posts=[PeerPost(like_count=5)]
    )
    assert profile.engagement_rate() == 0.0


def test_personal_account_error_is_explained(monkeypatch):
    from lumina.sources.peers import PeerLookupError, fetch_peer

    stub_discovery(monkeypatch, {"error": {"message": "Invalid user id"}})
    with pytest.raises(PeerLookupError, match="Business and Creator"):
        fetch_peer(live_config(), "someone")


def test_peer_lookup_requires_instagram_credentials():
    from lumina.sources.peers import PeerLookupError, fetch_peer

    with pytest.raises(PeerLookupError, match="IG_USER_ID"):
        fetch_peer(make_config(), "someone")


def test_peer_lookup_errors_never_carry_the_token(monkeypatch):
    import requests as _requests

    from lumina.sources.peers import PeerLookupError, fetch_peer

    def boom(*args, **kwargs):
        raise _requests.RequestException("failed ?access_token=EAAGsupersecret here")

    monkeypatch.setattr("lumina.sources.peers.requests.get", boom)
    with pytest.raises(PeerLookupError) as caught:
        fetch_peer(live_config(), "someone")
    assert "EAAGsupersecret" not in str(caught.value)


def test_peer_ideas_round_trip_to_disk(tmp_path):
    from lumina.models import Topic
    from lumina.store import load_peer_ideas, save_peer_ideas

    topics = [Topic(title="Process posts", angle="a", why_now="b", sources=["u"])]
    save_peer_ideas(topics, tmp_path)

    [loaded] = load_peer_ideas(tmp_path)
    assert loaded.title == "Process posts"


def test_missing_peer_ideas_file_is_not_an_error(tmp_path):
    from lumina.store import load_peer_ideas

    assert load_peer_ideas(tmp_path) == []


def test_peers_config_strips_leading_at_signs():
    config = make_config(content={"peers": ["@one", "two"]})
    assert config.peers == ["one", "two"]


# --- config ----------------------------------------------------------------


def test_unfilled_fields_are_detected_at_any_depth():
    config = make_config(voice={"tone": "FILL_ME", "never": ["fine", "FILL_ME"]})
    unfilled = config.unfilled_fields()
    assert "voice.tone" in unfilled
    assert "voice.never[1]" in unfilled


def test_shipped_config_parses():
    config = load_config()
    assert config.brand_name == "Lumina Presence"
    # This is the live brand config, not a template — once filled in, it
    # should have no FILL_ME placeholders left. If this starts failing,
    # someone reverted a field to the placeholder rather than a real value.
    assert config.unfilled_fields() == []


def test_missing_config_file_raises_a_readable_error(tmp_path):
    from lumina.config import ConfigError

    with pytest.raises(ConfigError, match="No config at"):
        load_config(tmp_path / "nope.yaml")
