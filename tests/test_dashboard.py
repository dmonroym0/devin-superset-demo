from fastapi.testclient import TestClient

from app.db import SessionRow
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

        response = client.get("/")

    assert response.status_code == 200
    assert "DEMO" in response.text
    assert "Issues seen" in response.text
    assert "PR opened" in response.text
    assert "Needs human" in response.text
    assert "Not reachable" in response.text
    assert "Automation rate" in response.text
    assert "Median time to PR" in response.text
    assert "42s" in response.text
    assert "runtime ceiling across all issues" in response.text
    assert "https://github.com/dmonroym0/superset/issues/101" in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    assert "<script>alert(1)" not in response.text
    assert "org default" in response.text
    assert ">fast<" in response.text
    assert "ACUs consumed" in response.text
    assert "2.5" in response.text
    assert "session-default" in response.text
    assert "session-fast" in response.text
    assert "javascript:alert(1)" not in response.text
    assert 'href="javascript:' not in response.text
    assert "session_started" in response.text
    assert '<meta http-equiv="refresh" content="5">' in response.text
    assert "<script" not in response.text
    assert "http://" not in response.text
    assert "cdn" not in response.text.lower()
