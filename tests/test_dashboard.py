import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.db import SessionRow
from app.i18n import flatten
from app.main import create_app
from app.models import Issue, IssueState, Stage


def test_dashboard_renders_database_rows_safely(test_settings, fake_github, fake_devin):
    app = create_app(
        test_settings,
        github=fake_github,
        devin=fake_devin,
        clock=lambda: 1_700_000_000,
    )
    with TestClient(app) as client:
        db = app.state.deps.db
        db.upsert_seen_issue(Issue(number=101, title="<script>alert(1)</script>", body=""), 1_699_999_900)
        db.transition(
            101,
            [IssueState.SEEN],
            IssueState.PR_OPENED,
            1_699_999_942,
            route_reason="reachable",
            pr_url="https://github.com/dmonroym0/superset/pull/201",
            pr_opened_at=1_699_999_942,
        )
        db.upsert_seen_issue(Issue(number=102, title="Needs review", body=""), 1_699_999_950)
        db.transition(
            102,
            [IssueState.SEEN],
            IssueState.NEEDS_HUMAN,
            1_699_999_960,
            route_reason="human decision required",
        )
        db.upsert_seen_issue(Issue(number=103, title="Other repository PR", body=""), 1_699_999_918)
        db.transition(
            103,
            [IssueState.SEEN],
            IssueState.PR_OPENED,
            1_699_999_960,
            route_reason="reachable",
            pr_url="https://github.com/other/repo/pull/202",
            pr_opened_at=1_699_999_960,
        )
        db.insert_session(
            SessionRow(
                session_id="session-default",
                issue_number=101,
                stage=Stage.FIX,
                status="running",
                status_detail="working",
                devin_mode=None,
                max_acu_limit=15,
                acus_consumed=2.5,
                url="https://app.devin.ai/sessions/session-default",
                created_at=1_699_999_960,
                updated_at=1_699_999_970,
            )
        )
        db.insert_session(
            SessionRow(
                session_id="session-fast",
                issue_number=102,
                stage=Stage.TRIAGE,
                status="blocked",
                status_detail=None,
                devin_mode="fast",
                max_acu_limit=5,
                acus_consumed=1,
                url="javascript:alert(1)",
                created_at=1_699_999_970,
                updated_at=1_699_999_980,
            )
        )
        app.state.deps.budget.reserve(101, Stage.FIX, 15, 1_699_999_960)
        db.add_event(101, "session_started", "Session started", 1_699_999_980)

        overview = client.get("/")
        issue = client.get("/issues/101")
        other_issue = client.get("/issues/103")
        sessions = client.get("/sessions")

    en = flatten(json.loads((Path(__file__).parents[1] / "app/i18n/en.json").read_text()))
    assert overview.status_code == issue.status_code == other_issue.status_code == sessions.status_code == 200
    assert en["demo.badge"] in overview.text
    for key in (
        "overview.issues_seen",
        "overview.prs_opened",
        "overview.needs_human",
        "overview.automation_rate",
        "overview.median_time_to_pr",
        "overview.acu_definition",
        "overview.pipeline_heading",
        "sync.heading",
    ):
        assert en[key] in overview.text
    assert en["units.seconds"].format(s=42) in overview.text
    assert "https://github.com/dmonroym0/superset/issues/101" in issue.text
    assert 'href="https://github.com/dmonroym0/superset/pull/201"' in issue.text
    assert 'href="https://github.com/other/repo/pull/202"' not in other_issue.text
    assert "https://github.com/other/repo/pull/202" not in other_issue.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in overview.text
    assert "<script>alert(1)" not in overview.text
    assert en["sessions.org_default"] in sessions.text
    assert ">fast<" in sessions.text
    assert en["sessions.col_consumed"] in sessions.text
    assert "2.5" in sessions.text
    assert "session-default" in sessions.text
    assert "session-fast" in sessions.text
    assert "javascript:alert(1)" not in sessions.text
    assert 'href="javascript:' not in sessions.text
    assert en["event.fallback"].format(kind="session_started") in issue.text
