import html
import json
import re
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import SessionRow
from app.i18n import flatten
from app.main import create_app
from app.models import Issue, Mode, Stage

ROOT = Path(__file__).parents[1]
HTML_PAGES = ("/", "/pipeline", "/sessions", "/cost")


def _catalog(locale: str) -> dict[str, str]:
    return flatten(json.loads((ROOT / f"app/i18n/{locale}.json").read_text()))


@pytest.fixture
def dashboard_client(test_settings, fake_github, fake_devin):
    settings = replace(test_settings, mode=Mode.DEMO, upstream_sync_enabled=False)
    app = create_app(
        settings,
        github=fake_github,
        devin=fake_devin,
        clock=lambda: 1_700_000_000,
    )
    with TestClient(app) as client:
        yield client, app


def test_dashboard_pages_render_with_localized_html_language(dashboard_client):
    client, _ = dashboard_client
    for path in HTML_PAGES:
        english = client.get(path, headers={"Accept-Language": "en"})
        spanish = client.get(path, headers={"Accept-Language": "es-MX"})

        assert english.status_code == spanish.status_code == 200
        assert '<html lang="en">' in english.text
        assert english.headers["content-language"] == "en"
        assert '<html lang="es-419">' in spanish.text
        assert spanish.headers["content-language"] == "es-419"


def test_language_redirects_reject_external_next_values(dashboard_client):
    client, _ = dashboard_client
    response = client.get("/lang/es?next=/pipeline", follow_redirects=False)
    assert response.status_code in {303, 307}
    assert response.headers["location"] == "/pipeline"
    assert response.cookies.get("forkfix_lang") == "es"

    for next_value in ("//evil.example", "https://evil.example"):
        response = client.get(
            f"/lang/es?next={next_value}",
            follow_redirects=False,
        )
        assert response.status_code in {303, 307}
        assert response.headers["location"] == "/"


def test_issue_views_localize_raw_log_and_missing_issue(dashboard_client):
    client, app = dashboard_client
    db = app.state.deps.db
    db.upsert_seen_issue(Issue(number=301, title="Seeded issue", body=""), 1_699_999_900)
    db.add_event(301, "sweep", "", 1_699_999_901)

    for locale, header in (("en", "en"), ("es", "es-MX")):
        response = client.get("/issues/301", headers={"Accept-Language": header})
        assert response.status_code == 200
        assert _catalog(locale)["issue.show_raw"] in response.text

        missing = client.get("/issues/999999", headers={"Accept-Language": header})
        assert missing.status_code == 404
        assert _catalog(locale)["issue.missing_title"] in html.unescape(missing.text)


def test_demo_badge_and_zero_metered_acu_display(dashboard_client):
    client, app = dashboard_client
    db = app.state.deps.db
    db.upsert_seen_issue(Issue(number=302, title="Finished session issue", body=""), 1_699_999_900)
    db.insert_session(
        SessionRow(
            session_id="finished-zero-acu",
            issue_number=302,
            stage=Stage.FIX,
            status="finished",
            status_detail=None,
            devin_mode=None,
            max_acu_limit=15,
            acus_consumed=0,
            url=None,
            created_at=1_699_999_910,
            updated_at=1_699_999_960,
            settled_at=1_699_999_960,
        )
    )

    overview = client.get("/")
    assert _catalog("en")["demo.badge"] in overview.text
    sessions = client.get("/sessions")
    cost = client.get("/cost")
    assert _catalog("en")["sessions.not_reported"] in sessions.text
    assert "$0" not in sessions.text
    assert "$0" not in cost.text


def test_sessions_page_sorts_newest_first_without_reordering_metrics(dashboard_client):
    client, app = dashboard_client
    db = app.state.deps.db
    db.upsert_seen_issue(Issue(number=303, title="Session order issue", body=""), 80)
    for session_id, created_at in (
        ("session-older", 100),
        ("session-newer-a", 200),
        ("session-newer-z", 200),
    ):
        db.insert_session(
            SessionRow(
                session_id=session_id,
                issue_number=303,
                stage=Stage.FIX,
                status="running",
                status_detail=None,
                devin_mode=None,
                max_acu_limit=15,
                acus_consumed=0,
                url=None,
                created_at=created_at,
                updated_at=created_at,
            )
        )

    metrics_before = [row["session_id"] for row in client.get("/metrics.json").json()["sessions"]]
    assert metrics_before == ["session-older", "session-newer-a", "session-newer-z"]

    page = client.get("/sessions")
    positions = [
        page.text.index(session_id) for session_id in ("session-newer-z", "session-newer-a", "session-older")
    ]
    assert positions == sorted(positions)
    metrics_after = [row["session_id"] for row in client.get("/metrics.json").json()["sessions"]]
    assert metrics_after == metrics_before


class _AssetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths: list[str] = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "link" and values.get("href"):
            self.paths.append(values["href"])
        elif tag == "script" and values.get("src"):
            self.paths.append(values["src"])


def test_manifest_icons_and_rendered_assets_are_local_and_work(dashboard_client):
    client, _ = dashboard_client
    manifest_response = client.get("/manifest.webmanifest")
    assert manifest_response.status_code == 200
    assert manifest_response.headers["content-type"].startswith("application/manifest+json")
    manifest = manifest_response.json()
    assert manifest["display"] == "standalone"
    assert manifest["start_url"] == "/"
    for icon in manifest["icons"]:
        assert icon["src"].startswith("/")
        response = client.get(icon["src"])
        assert response.status_code == 200
        assert response.headers["content-type"].split(";")[0] == icon["type"]

    referenced_assets = set()
    for path in HTML_PAGES:
        parser = _AssetParser()
        parser.feed(client.get(path).text)
        referenced_assets.update(parser.paths)
    assert referenced_assets
    for path in referenced_assets:
        assert path.startswith("/")
        assert client.get(path).status_code == 200

    css = client.get("/static/app.css")
    assert css.status_code == 200
    css_assets = re.findall(r"url\(\s*[\"']?([^)'\"\s]+)", css.text)
    assert css_assets
    for path in css_assets:
        assert path.startswith("/")
        assert client.get(path).status_code == 200
