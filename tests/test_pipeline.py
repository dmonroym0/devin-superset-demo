import asyncio
import json
from dataclasses import replace
from pathlib import Path

import httpx
from fastapi import FastAPI

from app import issue_actions
from app.budget import Budget
from app.config import Settings
from app.db import Database, SessionRow
from app.devin_client import DevinError
from app.fake_devin import FakeDevin
from app.fake_github import FakeGitHub
from app.github_client import GitHubError
from app.interfaces import Deps
from app.models import Issue, IssueState, LabelSpec, SessionInfo, Stage
from app.pipeline import register, tick
from app.playbooks import resolve_playbooks
from app.triage import start_triage

SEED_PATH = Path(__file__).resolve().parent.parent / "app" / "demo" / "seed_issues.json"
SYNTHETIC_BODY = "Package: example (pip)\nCurrent -> fixed: 1.0.0 -> 1.0.1 (patch)\nCVEs: CVE-0000-0001\n"


def seed_issues() -> dict[int, Issue]:
    issues = {
        item["number"]: Issue(item["number"], item["title"], item["body"], tuple(item.get("labels", ())))
        for item in json.loads(SEED_PATH.read_text())["issues"]
    }
    for number in (9001, 9002):
        issues[number] = Issue(number, f"synthetic #{number}", SYNTHETIC_BODY)
    return issues


class SeedGitHub:
    def __init__(self):
        self.issues = seed_issues()
        self.get_calls: list[int] = []

    async def get_issue(self, number: int) -> Issue:
        self.get_calls.append(number)
        await asyncio.sleep(0)
        return self.issues[number]

    async def list_open_issues_with_label(self, label: str) -> list[Issue]:
        return []

    async def ensure_labels(self, labels: tuple[LabelSpec, ...] | list[LabelSpec]) -> list[str]:
        return []

    async def add_labels(self, number: int, labels: list[str]) -> None:
        return None

    async def remove_label(self, number: int, label: str) -> None:
        return None

    async def create_comment(self, number: int, body: str) -> str:
        return ""

    async def close_issue(self, number: int, reason: str = "not_planned") -> None:
        return None

    async def create_issue(self, title: str, body: str, labels: list[str]) -> Issue:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None


class RecordingActions:
    def __init__(self):
        self.calls: list[tuple] = []

    def names(self, number: int | None = None) -> list[str]:
        return [call[0] for call in self.calls if number is None or call[1] == number]

    async def mark_in_progress(self, deps, number) -> None:
        self.calls.append(("mark_in_progress", number))

    async def apply_route(self, deps, number, decision, result, *, rejected_pr_urls=()) -> str | None:
        self.calls.append(("apply_route", number, decision, result, tuple(rejected_pr_urls)))
        return None

    async def mark_pr_opened(self, deps, number, pr_urls) -> None:
        self.calls.append(("mark_pr_opened", number, tuple(pr_urls)))

    async def mark_needs_human(self, deps, number, reason) -> None:
        self.calls.append(("mark_needs_human", number, reason))

    async def mark_queued_budget(self, deps, number, committed, ceiling) -> None:
        self.calls.append(("mark_queued_budget", number, committed, ceiling))

    async def clear_queued_budget(self, deps, number) -> None:
        self.calls.append(("clear_queued_budget", number))


class Clock:
    def __init__(self, now: float = 1_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


async def make_deps(tmp_path, devin=None, **env) -> Deps:
    settings = Settings.from_env({"DB_PATH": str(tmp_path / "pipeline.db"), **env})
    db = Database(settings.db_path)
    db.init_schema()
    deps = Deps(
        settings=settings,
        db=db,
        budget=Budget(db, settings.acu_ceiling),
        github=SeedGitHub(),
        devin=devin or FakeDevin.from_scenarios(),
        clock=Clock(),
    )
    deps.playbooks = await resolve_playbooks(settings, deps.devin)
    return deps


def seed(deps: Deps, *numbers: int) -> None:
    for number in numbers:
        issue = deps.github.issues[number]
        if deps.settings.trigger_label not in issue.labels:
            issue = replace(issue, labels=(*issue.labels, deps.settings.trigger_label))
            deps.github.issues[number] = issue
        deps.db.upsert_seen_issue(issue, deps.clock())


def add_settled_session(deps: Deps, issue_number: int, stage: Stage, *, created_at: float | None = None):
    now = deps.clock()
    session_id = f"settled-{stage.value}-{issue_number}"
    row = SessionRow(
        session_id=session_id,
        issue_number=issue_number,
        stage=stage,
        status="running",
        status_detail="working",
        devin_mode=None,
        max_acu_limit=deps.settings.triage_acu_cap if stage is Stage.TRIAGE else deps.settings.fix_acu_cap,
        acus_consumed=0.0,
        url=None,
        created_at=now if created_at is None else created_at,
        updated_at=now,
        settled_at=now,
        archived=stage is Stage.TRIAGE,
    )
    deps.db.insert_session(row)
    return row


def record_create_then_timeout(devin, *, stages: tuple[str, ...] | None = None) -> list[str]:
    created: list[str] = []
    create_session = devin.create_session

    async def create_and_lose_response(request):
        info = await create_session(request)
        if stages is None or any(f"stage-{stage}" in request.tags for stage in stages):
            created.append(info.session_id)
            raise httpx.ReadTimeout("session created but response was lost")
        return info

    devin.create_session = create_and_lose_response
    return created


async def run_ticks(deps: Deps, actions, ticks: int = 10, step: float = 1.0, check=None) -> None:
    for _ in range(ticks):
        await tick(deps, actions)
        if check:
            check()
        deps.clock.now += step


def http_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://devin.test/sessions/demo-triage-2-1")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("poll failed", request=request, response=response)


async def run_ticks_with_poll_error(tmp_path, error, ticks=8):
    devin = FakeDevin.from_scenarios()
    get_session = devin.get_session
    should_fail = True

    async def fail_once(session_id):
        nonlocal should_fail
        if should_fail:
            should_fail = False
            raise error
        return await get_session(session_id)

    devin.get_session = fail_once
    deps = await make_deps(tmp_path, devin=devin)
    actions = RecordingActions()
    seed(deps, 2)
    await run_ticks(deps, actions, ticks=ticks)
    return deps, devin


def state(deps: Deps, number: int) -> IssueState:
    return deps.db.get_issue(number).state


async def test_seeded_scenarios_end_states_and_archiving(tmp_path):
    deps = await make_deps(tmp_path)
    actions = RecordingActions()
    seed(deps, 2, 3, 4)
    await run_ticks(deps, actions)

    assert state(deps, 2) is IssueState.NOT_REACHABLE
    assert state(deps, 3) is IssueState.PR_OPENED
    assert deps.db.get_issue(3).pr_url == "https://github.com/dmonroym0/superset/pull/103"
    assert state(deps, 4) is IssueState.NEEDS_HUMAN
    assert "major version bump" in deps.db.get_issue(4).route_reason
    assert ("mark_pr_opened", 3, ("https://github.com/dmonroym0/superset/pull/103",)) in actions.calls

    devin = deps.devin
    triage_ids = {row.session_id for row in deps.db.list_sessions(stage=Stage.TRIAGE)}
    fix_ids = {row.session_id for row in deps.db.list_sessions(stage=Stage.FIX)}
    assert triage_ids == {"demo-triage-2-1", "demo-triage-3-1", "demo-triage-4-1"}
    assert fix_ids == {"demo-fix-3-1"}
    assert set(devin.archived) == triage_ids
    assert not fix_ids & set(devin.archived)
    assert all(row.archived for row in deps.db.list_sessions(stage=Stage.TRIAGE))
    assert deps.db.get_issue(3).package and deps.db.get_issue(3).bump_kind is not None


async def test_tick_refreshes_archived_triage_session_status_once(tmp_path):
    deps = await make_deps(tmp_path)
    actions = RecordingActions()
    seed(deps, 2)
    deps.db.transition(2, [IssueState.SEEN], IssueState.NEEDS_HUMAN, deps.clock())
    row = add_settled_session(deps, 2, Stage.TRIAGE)
    calls = []

    async def get_suspended_session(session_id):
        calls.append(session_id)
        return SessionInfo(session_id, status="suspended", status_detail="archived by Devin")

    deps.devin.get_session = get_suspended_session

    await tick(deps, actions)

    updated = deps.db.get_session(row.session_id)
    assert updated.status == "suspended"
    assert updated.archived is True
    assert deps.db.get_issue(2).state is IssueState.NEEDS_HUMAN

    await tick(deps, actions)
    assert calls == [row.session_id]


async def test_settled_session_refresh_keeps_structured_output(tmp_path):
    deps = await make_deps(tmp_path)
    actions = RecordingActions()
    seed(deps, 2)
    deps.db.transition(2, [IssueState.SEEN], IssueState.TRIAGED, deps.clock())
    row = add_settled_session(deps, 2, Stage.TRIAGE)
    structured_output = {"cves": [], "notes": "preserve triage evidence"}
    deps.db.update_session(row.session_id, structured_output=structured_output)

    async def get_suspended_session(session_id):
        return SessionInfo(session_id, status="suspended", structured_output=None)

    deps.devin.get_session = get_suspended_session

    await tick(deps, actions)

    updated = deps.db.get_session(row.session_id)
    assert updated.status == "suspended"
    assert updated.structured_output == structured_output


async def test_tick_refreshes_settled_fix_status_without_side_effects(tmp_path):
    deps = await make_deps(tmp_path)
    actions = RecordingActions()
    seed(deps, 3)
    deps.db.transition(
        3,
        [IssueState.SEEN],
        IssueState.PR_OPENED,
        deps.clock(),
        pr_url="https://github.com/dmonroym0/superset/pull/903",
    )
    row = add_settled_session(deps, 3, Stage.FIX)
    events_before = deps.db.list_events()
    actions_before = list(actions.calls)
    calls = []

    async def get_updated_status(session_id):
        calls.append(session_id)
        return SessionInfo(session_id, status="running", status_detail="waiting on review")

    deps.devin.get_session = get_updated_status

    await tick(deps, actions)

    assert deps.db.get_session(row.session_id).status_detail == "waiting on review"
    assert deps.db.get_issue(3).state is IssueState.PR_OPENED
    assert deps.db.list_events() == events_before
    assert actions.calls == actions_before
    assert deps.github.get_calls == []
    assert calls == [row.session_id]


async def test_tick_skips_settled_session_refresh_after_hard_timeout(tmp_path):
    deps = await make_deps(tmp_path)
    actions = RecordingActions()
    seed(deps, 3)
    deps.db.transition(3, [IssueState.SEEN], IssueState.PR_OPENED, deps.clock())
    created_at = deps.clock() - deps.settings.hard_timeout_s - 1
    row = add_settled_session(deps, 3, Stage.FIX, created_at=created_at)
    calls = []

    async def get_session(session_id):
        calls.append(session_id)
        return SessionInfo(session_id, status="suspended")

    deps.devin.get_session = get_session

    await tick(deps, actions)

    assert deps.db.get_session(row.session_id).status == "running"
    assert calls == []


async def test_tick_continues_when_settled_session_refresh_fails(tmp_path):
    deps = await make_deps(tmp_path)
    actions = RecordingActions()
    seed(deps, 3)
    deps.db.transition(3, [IssueState.SEEN], IssueState.PR_OPENED, deps.clock())
    row = add_settled_session(deps, 3, Stage.FIX)
    events_before = deps.db.list_events()
    calls = []

    async def fail_get_session(session_id):
        calls.append(session_id)
        raise RuntimeError(f"failed to poll {session_id}")

    deps.devin.get_session = fail_get_session

    await tick(deps, actions)

    assert deps.db.get_issue(3).state is IssueState.PR_OPENED
    assert deps.db.get_session(row.session_id).status == "running"
    assert deps.db.list_events() == events_before
    assert actions.calls == []
    assert calls == [row.session_id]


async def test_transient_poll_error_retries_without_moving_issue_to_error(tmp_path):
    devin = FakeDevin.from_scenarios()
    get_session = devin.get_session
    calls = 0

    async def fail_once(session_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise DevinError(503, "GET", f"/sessions/{session_id}")
        return await get_session(session_id)

    devin.get_session = fail_once
    deps = await make_deps(tmp_path, devin=devin)
    actions = RecordingActions()
    seed(deps, 2)

    await run_ticks(deps, actions, ticks=8)

    assert state(deps, 2) is IssueState.NOT_REACHABLE
    assert deps.db.get_issue(2).last_error is None
    events = [event for event in deps.db.list_events(2) if event.kind == "devin_poll_failed"]
    assert len(events) == 1
    assert events[0].detail == "DevinError 503"


async def test_poll_not_found_moves_issue_to_error(tmp_path):
    deps, _ = await run_ticks_with_poll_error(tmp_path, DevinError(404, "GET", "/sessions/demo-triage-2-1"))

    assert state(deps, 2) is IssueState.ERROR
    assert deps.db.get_issue(2).last_error
    assert any(event.kind == "pipeline_error" for event in deps.db.list_events(2))


async def test_zero_status_poll_error_is_transient(tmp_path):
    deps, _ = await run_ticks_with_poll_error(tmp_path, DevinError(0, "GET", "/sessions/demo-triage-2-1"))

    assert state(deps, 2) is IssueState.NOT_REACHABLE
    assert any(event.kind == "devin_poll_failed" for event in deps.db.list_events(2))


async def test_transport_poll_error_is_transient(tmp_path):
    deps, _ = await run_ticks_with_poll_error(tmp_path, httpx.ConnectError("network down"))

    assert state(deps, 2) is IssueState.NOT_REACHABLE
    assert any(event.kind == "devin_poll_failed" for event in deps.db.list_events(2))


async def test_http_status_poll_server_error_is_transient(tmp_path):
    deps, _ = await run_ticks_with_poll_error(tmp_path, http_status_error(503))

    assert state(deps, 2) is IssueState.NOT_REACHABLE
    assert any(event.kind == "devin_poll_failed" for event in deps.db.list_events(2))


async def test_triage_hard_timeout_escalates_on_transient_poll_error(tmp_path):
    deps = await make_deps(tmp_path, HARD_TIMEOUT_S="3")
    actions = RecordingActions()
    seed(deps, 2)

    async def fail_poll(session_id):
        raise DevinError(503, "GET", f"/sessions/{session_id}")

    deps.devin.get_session = fail_poll
    await tick(deps, actions)
    session = deps.db.list_sessions(stage=Stage.TRIAGE, issue_number=2)[0]

    assert state(deps, 2) is IssueState.TRIAGING
    assert deps.db.get_session(session.session_id).settled_at is None
    assert any(event.kind == "devin_poll_failed" for event in deps.db.list_events(2))

    deps.clock.now = session.created_at + 2
    await tick(deps, actions)
    assert state(deps, 2) is IssueState.TRIAGING

    deps.clock.now = session.created_at + 4
    await tick(deps, actions)

    assert state(deps, 2) is IssueState.NEEDS_HUMAN
    assert deps.db.get_session(session.session_id).settled_at is not None
    assert any(event.kind == "escalated" for event in deps.db.list_events(2))


async def test_fix_hard_timeout_escalates_on_transient_poll_error(tmp_path):
    deps = await make_deps(tmp_path, HARD_TIMEOUT_S="3")
    actions = RecordingActions()
    seed(deps, 1)

    for _ in range(10):
        await tick(deps, actions)
        if state(deps, 1) is IssueState.FIXING:
            break
    assert state(deps, 1) is IssueState.FIXING
    session = deps.db.list_sessions(stage=Stage.FIX, issue_number=1)[0]

    async def fail_poll(session_id):
        raise DevinError(503, "GET", f"/sessions/{session_id}")

    deps.devin.get_session = fail_poll
    deps.clock.now = session.created_at
    await tick(deps, actions)

    assert state(deps, 1) is IssueState.FIXING
    assert deps.db.get_session(session.session_id).settled_at is None
    assert any(event.kind == "devin_poll_failed" for event in deps.db.list_events(1))

    deps.clock.now = session.created_at + 2
    await tick(deps, actions)
    assert state(deps, 1) is IssueState.FIXING

    deps.clock.now = session.created_at + 4
    await tick(deps, actions)

    assert state(deps, 1) is IssueState.NEEDS_HUMAN
    assert deps.db.get_session(session.session_id).settled_at is not None
    assert any(event.kind == "escalated" for event in deps.db.list_events(1))


async def test_every_create_request_has_caps_tags_schema_and_repo(tmp_path):
    deps = await make_deps(tmp_path)
    seed(deps, 1, 2, 3, 4, 5)
    await run_ticks(deps, RecordingActions(), ticks=12)
    requests = deps.devin.requests
    assert requests
    triage_schema = (await deps.devin.get_playbook("playbook-demo-triage")).structured_output_schema
    for request in requests:
        number = next(tag for tag in request.tags if tag.startswith("issue-"))
        assert "devin-superset-demo" in request.tags and number
        assert request.repos == ("dmonroym0/superset",)
        assert request.to_payload()["repos"] == ["dmonroym0/superset"]
        if "stage-triage" in request.tags:
            assert request.max_acu_limit == 5
            assert request.structured_output_schema == triage_schema
            assert request.playbook_id == "playbook-demo-triage"
            assert request.structured_output_required is True
        else:
            assert "stage-fix" in request.tags
            assert request.max_acu_limit == 15
            assert request.playbook_id == "playbook-demo-fix"
    assert {r.title for r in requests if "stage-fix" in r.tags} == {
        "fix dmonroym0/superset#1",
        "fix dmonroym0/superset#3",
    }


async def test_restart_preserves_ambiguous_unattached_reservation(tmp_path):
    deps = await make_deps(tmp_path)
    actions = RecordingActions()
    seed(deps, 2)
    assert deps.db.transition(2, [IssueState.SEEN], IssueState.TRIAGING, deps.clock())
    definite_reservation = deps.budget.reserve(2, Stage.TRIAGE, deps.settings.triage_acu_cap, deps.clock())
    reservation = deps.budget.reserve(2, Stage.TRIAGE, deps.settings.triage_acu_cap, deps.clock())
    assert definite_reservation is not None
    assert reservation is not None
    assert deps.budget.mark_create_started(reservation, deps.clock()) is True

    app = FastAPI()
    register(app, deps, actions)
    await deps.startup[0]()
    await tick(deps, actions)

    row = deps.db.get_issue(2)
    assert row.state is IssueState.NEEDS_HUMAN
    assert row.route_reason == "session creation outcome unknown; ACU reservation kept until reviewed"
    assert deps.budget.committed() == deps.settings.triage_acu_cap
    assert any(event.kind == "create_ambiguous_after_restart" for event in deps.db.list_events(2))
    assert actions.names(2).count("mark_needs_human") == 1
    assert deps.devin.requests == []


async def test_triage_that_opens_a_pr_is_rejected(tmp_path):
    deps = await make_deps(tmp_path)
    actions = RecordingActions()
    seed(deps, 9001)
    await run_ticks(deps, actions, ticks=4)
    assert state(deps, 9001) is IssueState.NEEDS_HUMAN
    routes = [call for call in actions.calls if call[0] == "apply_route" and call[1] == 9001]
    assert len(routes) == 1
    _, _, decision, result, rejected = routes[0]
    assert rejected == ("https://github.com/dmonroym0/superset/pull/9001",)
    assert result is None and decision.action.value == "needs_human"
    assert any(event.kind == "triage_rejected" for event in deps.db.list_events(9001))
    assert not [r for r in deps.devin.requests if "stage-fix" in r.tags]
    assert "demo-triage-9001-1" in deps.devin.archived


async def test_session_that_never_settles_is_nudged_once_then_escalated(tmp_path):
    deps = await make_deps(tmp_path, SOFT_TIMEOUT_S="10", HARD_TIMEOUT_S="30")
    actions = RecordingActions()
    seed(deps, 9002)
    await run_ticks(deps, actions, ticks=25)
    assert state(deps, 9002) is IssueState.TRIAGING
    assert len(deps.devin.messages) == 1
    assert deps.devin.messages[0][0] == "demo-triage-9002-1"
    await run_ticks(deps, actions, ticks=10)
    assert state(deps, 9002) is IssueState.NEEDS_HUMAN
    assert len(deps.devin.messages) == 1
    assert actions.names(9002).count("mark_needs_human") == 1
    assert deps.db.get_session("demo-triage-9002-1").settled_at is not None


async def test_budget_queues_and_retries_without_exceeding_ceiling(tmp_path):
    deps = await make_deps(tmp_path, ACU_CEILING="20")
    actions = RecordingActions()
    seed(deps, 1, 2, 3, 4, 5)

    def within_ceiling():
        assert deps.budget.committed() <= 20

    await run_ticks(deps, actions, ticks=6, check=within_ceiling)
    assert state(deps, 5) is IssueState.QUEUED_BUDGET
    assert state(deps, 1) is IssueState.TRIAGED and state(deps, 3) is IssueState.TRIAGED
    assert actions.names(5).count("mark_queued_budget") == 1
    assert actions.names(1).count("mark_queued_budget") == 1
    assert actions.names(1).count("apply_route") == 1
    assert len([r for r in deps.devin.requests if "issue-5" in r.tags]) == 0

    with deps.db._lock:
        ids = [row[0] for row in deps.db._connection.execute("SELECT id FROM ledger WHERE cancelled=0")]
    for reservation_id in ids:
        deps.budget.cancel(reservation_id)

    await run_ticks(deps, actions, ticks=8, check=within_ceiling)
    assert "clear_queued_budget" in actions.names(5)
    assert "clear_queued_budget" in actions.names(1)
    assert state(deps, 1) is IssueState.PR_OPENED
    assert state(deps, 5) is IssueState.NEEDS_HUMAN
    assert state(deps, 3) is IssueState.TRIAGED
    assert actions.names(3).count("mark_queued_budget") == 1


async def test_pipeline_retries_failed_budget_comment(tmp_path, monkeypatch):
    deps = await make_deps(tmp_path, ACU_CEILING="5", TRIAGE_ACU_CAP="5", FIX_ACU_CAP="5")
    github = FakeGitHub.from_seed()
    deps.github = github
    seed(deps, 1, 2)
    original_create_comment = github.create_comment
    failed = False

    async def fail_once(number, body):
        nonlocal failed
        if number == 2 and body.startswith("Queued for the next retry:") and not failed:
            failed = True
            raise GitHubError(503, "POST", "/repos/dmonroym0/superset/issues/2/comments")
        return await original_create_comment(number, body)

    monkeypatch.setattr(github, "create_comment", fail_once)
    await run_ticks(deps, issue_actions, ticks=4)

    queue_comments = [
        comment for comment in github.comments[2] if comment.startswith("Queued for the next retry:")
    ]
    events = deps.db.list_events(issue_number=2)
    assert failed
    assert len(queue_comments) == 1
    assert any(event.kind == "queued_budget_comment_failed" for event in events)
    assert any(event.kind == "queued_budget_commented" for event in events)


async def test_concurrent_ticks_create_one_triage_session(tmp_path):
    deps = await make_deps(tmp_path)
    actions = RecordingActions()
    seed(deps, 2)
    await asyncio.gather(tick(deps, actions), tick(deps, actions))
    assert len([r for r in deps.devin.requests if "issue-2" in r.tags]) == 1

    seed(deps, 3)
    row = deps.db.get_issue(3)
    await asyncio.gather(start_triage(deps, row, actions), start_triage(deps, row, actions))
    assert len([r for r in deps.devin.requests if "issue-3" in r.tags]) == 1
    assert deps.budget.committed() == 10


class ExplodingGitHub(SeedGitHub):
    async def get_issue(self, number: int) -> Issue:
        raise RuntimeError("boom token=ghp_should_not_matter")


async def test_per_issue_exception_moves_issue_to_error_and_continues(tmp_path):
    deps = await make_deps(tmp_path)
    issues = deps.github.issues
    deps.github = ExplodingGitHub()
    deps.github.issues = issues
    seed(deps, 1, 2)
    await tick(deps, RecordingActions())
    for number in (1, 2):
        row = deps.db.get_issue(number)
        assert row.state is IssueState.ERROR
        assert row.last_error.startswith("RuntimeError")


async def test_register_appends_startup_and_worker(tmp_path):
    deps = await make_deps(tmp_path)
    deps.playbooks = None
    actions = RecordingActions()
    register(FastAPI(), deps, actions)
    assert len(deps.startup) == 1 and len(deps.background) == 1
    await deps.startup[0]()
    assert deps.playbooks.triage.playbook_id == "playbook-demo-triage"

    seed(deps, 2)
    task = asyncio.create_task(deps.background[0]())
    deps.wake.set()
    for _ in range(50):
        await asyncio.sleep(0.01)
        if deps.devin.requests:
            break
    task.cancel()
    assert deps.devin.requests


async def test_register_without_issue_actions_imports_module_directly(tmp_path):
    deps = await make_deps(tmp_path)
    app = FastAPI()
    register(app, deps)
    from app import issue_actions

    assert app.state.issue_actions is issue_actions
