import threading
from copy import deepcopy
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import SessionRow
from app.fake_devin import FakeDevin as ScenarioFakeDevin
from app.fake_github import FakeGitHub as SeededFakeGitHub
from app.main import create_app
from app.models import MANAGED_LABELS, IssueState, SessionRequest, Stage
from app.pipeline import tick
from app.playbooks import SchemaMismatch


def test_health_metrics_labels_and_lifecycle(fake_github, fake_devin, test_settings):
    app = create_app(test_settings, github=fake_github, devin=fake_devin, clock=lambda: 100.0)
    startup_ran = threading.Event()
    background_cancelled = threading.Event()

    async def startup_hook():
        startup_ran.set()

    async def background_worker():
        import asyncio

        try:
            await asyncio.Event().wait()
        finally:
            background_cancelled.set()

    app.state.deps.startup.append(startup_hook)
    app.state.deps.background.append(background_worker)

    with TestClient(app) as client:
        assert startup_ran.wait(timeout=1)
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json() == {"status": "ok", "mode": "demo"}
        metrics = client.get("/metrics.json")
        assert metrics.status_code == 200
        payload = metrics.json()
        assert set(payload) == {
            "generated_at",
            "mode",
            "issues",
            "automation_rate",
            "median_time_to_pr_s",
            "acu",
            "sessions",
            "issues_detail",
        }
        assert set(payload["issues"]) == {
            "seen",
            "in_flight",
            "pr_opened",
            "needs_human",
            "not_reachable",
            "queued_budget",
            "cancelled",
            "error",
        }
        assert set(payload["acu"]) == {"committed", "ceiling", "remaining", "consumed_metered"}

    assert fake_github.ensure_labels_calls == [MANAGED_LABELS]
    assert background_cancelled.wait(timeout=1)
    assert fake_github.closed
    assert fake_devin.closed


def test_label_setup_failure_does_not_prevent_startup(fake_github, fake_devin, test_settings):
    async def failing_ensure_labels(labels):
        raise RuntimeError("hidden-token-value")

    fake_github.ensure_labels = failing_ensure_labels
    app = create_app(test_settings, github=fake_github, devin=fake_devin)

    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200


def test_demo_startup_rejects_triage_schema_mismatch(fake_github, fake_devin, test_settings):
    triage = fake_devin.playbooks[0]
    schema = deepcopy(triage.structured_output_schema)
    schema["properties"]["cves"]["items"]["properties"]["confidence"]["enum"].append("unexpected")
    fake_devin.playbooks[0] = replace(triage, structured_output_schema=schema)
    app = create_app(test_settings, github=fake_github, devin=fake_devin)

    with (
        pytest.raises(
            SchemaMismatch,
            match=r"properties\.cves\.items\.properties\.confidence\.enum",
        ),
        TestClient(app),
    ):
        pass


def _recovery_app(test_settings):
    github = SeededFakeGitHub.from_seed()
    devin = ScenarioFakeDevin.from_scenarios()
    app = create_app(test_settings, github=github, devin=devin, clock=lambda: 100.0)
    app.state.deps.background.clear()
    return app, github, devin


async def test_startup_recovers_triaging_issue_and_reprocesses_it(
    test_settings,
):
    app, github, devin = _recovery_app(test_settings)
    deps = app.state.deps
    deps.db.init_schema()
    deps.db.upsert_seen_issue(github.issues[2], deps.clock())
    assert deps.db.transition(2, [IssueState.SEEN], IssueState.TRIAGING, deps.clock())
    pre_reservation = deps.budget.committed()
    assert deps.budget.reserve(2, Stage.TRIAGE, 5, deps.clock()) is not None
    assert deps.budget.committed() == pre_reservation + 5

    with TestClient(app, client=("127.0.0.1", 50000)):
        assert deps.db.get_issue(2).state is IssueState.SEEN
        assert deps.budget.committed() == pre_reservation
        recovered = [event for event in deps.db.list_events(2) if event.kind == "recovered_after_restart"]
        assert len(recovered) == 1

        for _ in range(8):
            await tick(deps, app.state.issue_actions)

        assert deps.db.get_issue(2).state is IssueState.NOT_REACHABLE
        triage_requests = [
            request
            for request in devin.requests
            if "issue-2" in request.tags and "stage-triage" in request.tags
        ]
        assert len(triage_requests) == 1
        assert (
            len([event for event in deps.db.list_events(2) if event.kind == "recovered_after_restart"]) == 1
        )


async def test_startup_recovers_fixing_issue_to_triaged(test_settings):
    app, github, _ = _recovery_app(test_settings)
    deps = app.state.deps
    deps.db.init_schema()
    deps.db.upsert_seen_issue(github.issues[3], deps.clock())
    assert deps.db.transition(3, [IssueState.SEEN], IssueState.FIXING, deps.clock())
    assert deps.budget.reserve(3, Stage.FIX, 15, deps.clock()) is not None

    with TestClient(app, client=("127.0.0.1", 50000)):
        assert deps.db.get_issue(3).state is IssueState.TRIAGED
        assert deps.budget.committed() == 0
        recovered = [event for event in deps.db.list_events(3) if event.kind == "recovered_after_restart"]
        assert len(recovered) == 1


async def test_startup_preserves_triaging_issue_with_active_session(test_settings):
    app, github, _ = _recovery_app(test_settings)
    deps = app.state.deps
    deps.db.init_schema()
    deps.db.upsert_seen_issue(github.issues[2], deps.clock())
    assert deps.db.transition(2, [IssueState.SEEN], IssueState.TRIAGING, deps.clock())
    reservation = deps.budget.reserve(2, Stage.TRIAGE, 5, deps.clock())
    assert reservation is not None
    assert deps.budget.attach(reservation, "active-triage-2")
    deps.db.insert_session(
        SessionRow(
            session_id="active-triage-2",
            issue_number=2,
            stage=Stage.TRIAGE,
            status="running",
            status_detail="working",
            devin_mode=None,
            max_acu_limit=5,
            acus_consumed=0.0,
            url=None,
            created_at=deps.clock(),
            updated_at=deps.clock(),
        )
    )

    with TestClient(app, client=("127.0.0.1", 50000)):
        assert deps.db.get_issue(2).state is IssueState.TRIAGING
        assert deps.db.get_session("active-triage-2").settled_at is None
        assert deps.budget.committed() == 5
        assert not [event for event in deps.db.list_events(2) if event.kind == "recovered_after_restart"]


async def test_startup_readopts_attached_triage_session_and_processes_it(test_settings):
    settings = replace(test_settings, devin_mode_triage="fast")
    app, github, devin = _recovery_app(settings)
    deps = app.state.deps
    existing_session = await devin.create_session(
        SessionRequest(
            prompt="triage issue",
            title="triage dmonroym0/superset#3",
            playbook_id="playbook-demo-triage",
            max_acu_limit=settings.triage_acu_cap,
            tags=("devin-superset-demo", "issue-3", "stage-triage"),
            devin_mode=settings.devin_mode_triage,
        )
    )
    assert existing_session.session_id == "demo-triage-3-1"
    devin.requests.clear()

    deps.db.init_schema()
    deps.db.upsert_seen_issue(github.issues[3], deps.clock())
    assert deps.db.transition(3, [IssueState.SEEN], IssueState.TRIAGING, deps.clock())
    reservation = deps.budget.reserve(3, Stage.TRIAGE, settings.triage_acu_cap, deps.clock())
    assert reservation is not None
    assert deps.budget.attach(reservation, existing_session.session_id)

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        assert deps.db.get_issue(3).state is IssueState.TRIAGING
        assert deps.budget.committed() == settings.triage_acu_cap
        adopted = deps.db.get_session(existing_session.session_id)
        assert adopted is not None
        assert adopted.max_acu_limit == settings.triage_acu_cap
        assert adopted.devin_mode == "fast"
        assert adopted.created_at == deps.clock()
        assert adopted.url is None
        assert any(
            event.kind == "readopted_after_restart" and event.detail == existing_session.session_id
            for event in deps.db.list_events(3)
        )

        metrics = client.get("/metrics.json")
        assert metrics.status_code == 200
        adopted_metric = next(
            session
            for session in metrics.json()["sessions"]
            if session["session_id"] == existing_session.session_id
        )
        assert adopted_metric["url"] is None
        board = client.get("/")
        assert board.status_code == 200
        assert existing_session.session_id in board.text

        for _ in range(8):
            await tick(deps, app.state.issue_actions)

        assert deps.db.get_issue(3).state is IssueState.PR_OPENED
        triage_requests = [
            request
            for request in devin.requests
            if "issue-3" in request.tags and "stage-triage" in request.tags
        ]
        assert triage_requests == []
        fix_requests = [
            request for request in devin.requests if "issue-3" in request.tags and "stage-fix" in request.tags
        ]
        assert len(fix_requests) == 1
        assert deps.budget.committed() >= settings.triage_acu_cap


def test_demo_and_live_use_distinct_databases_in_same_data_dir(tmp_path):
    data_dir = str(tmp_path)
    demo_settings = Settings.from_env({"DATA_DIR": data_dir})
    live_settings = Settings.from_env({"APP_MODE": "live", "DATA_DIR": data_dir})
    assert demo_settings.db_path != live_settings.db_path

    demo_github = SeededFakeGitHub.from_seed()
    demo_app = create_app(
        demo_settings,
        github=demo_github,
        devin=ScenarioFakeDevin.from_scenarios(),
        clock=lambda: 100.0,
    )
    demo_app.state.deps.background.clear()

    with TestClient(demo_app):
        demo_app.state.deps.db.upsert_seen_issue(demo_github.issues[2], 100.0)
        assert demo_app.state.deps.db.get_issue(2) is not None

    live_app = create_app(
        live_settings,
        github=SeededFakeGitHub.from_seed(),
        devin=ScenarioFakeDevin.from_scenarios(),
        clock=lambda: 100.0,
    )
    live_app.state.deps.background.clear()

    with TestClient(live_app) as client:
        assert client.get("/metrics.json").json()["issues"]["seen"] == 0


def test_startup_rejects_database_claimed_by_another_mode(tmp_path):
    db_path = str(tmp_path / "shared.db")
    demo_settings = Settings.from_env({"DB_PATH": db_path})
    demo_app = create_app(
        demo_settings,
        github=SeededFakeGitHub.from_seed(),
        devin=ScenarioFakeDevin.from_scenarios(),
    )
    demo_app.state.deps.background.clear()

    with TestClient(demo_app):
        pass

    live_settings = Settings.from_env({"APP_MODE": "live", "DB_PATH": db_path})
    live_app = create_app(
        live_settings,
        github=SeededFakeGitHub.from_seed(),
        devin=ScenarioFakeDevin.from_scenarios(),
    )
    live_app.state.deps.background.clear()

    with pytest.raises(Exception) as error, TestClient(live_app):
        pass

    assert type(error.value).__name__ == "DatabaseModeMismatch"
    assert str(error.value) == (
        f"database {db_path} belongs to demo mode, refusing to start in live; "
        "use a different DB_PATH/DATA_DIR"
    )
