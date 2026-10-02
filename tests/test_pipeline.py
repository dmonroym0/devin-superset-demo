import asyncio
import importlib.util
import json
from pathlib import Path

import pytest
from fastapi import FastAPI

from app.budget import Budget
from app.config import Settings
from app.db import Database
from app.fake_devin import FakeDevin
from app.interfaces import Deps
from app.models import Issue, IssueState, LabelSpec, Stage
from app.pipeline import NoopIssueActions, register, tick
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


class RecordingActions(NoopIssueActions):
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
        deps.db.upsert_seen_issue(deps.github.issues[number], deps.clock())


async def run_ticks(deps: Deps, actions, ticks: int = 10, step: float = 1.0, check=None) -> None:
    for _ in range(ticks):
        await tick(deps, actions)
        if check:
            check()
        deps.clock.now += step


def state(deps: Deps, number: int) -> IssueState:
    return deps.db.get_issue(number).state


async def test_seeded_scenarios_end_states_and_archiving(tmp_path):
    deps = await make_deps(tmp_path)
    actions = RecordingActions()
    seed(deps, 2, 3, 4)
    await run_ticks(deps, actions)

    assert state(deps, 2) is IssueState.NOT_REACHABLE
    assert state(deps, 3) is IssueState.PR_OPENED
    assert deps.db.get_issue(3).pr_url == "https://demo.invalid/dmonroym0/superset/pull/103"
    assert state(deps, 4) is IssueState.NEEDS_HUMAN
    assert "major version bump" in deps.db.get_issue(4).route_reason
    assert ("mark_pr_opened", 3, ("https://demo.invalid/dmonroym0/superset/pull/103",)) in actions.calls

    devin = deps.devin
    triage_ids = {row.session_id for row in deps.db.list_sessions(stage=Stage.TRIAGE)}
    fix_ids = {row.session_id for row in deps.db.list_sessions(stage=Stage.FIX)}
    assert triage_ids == {"demo-triage-2-1", "demo-triage-3-1", "demo-triage-4-1"}
    assert fix_ids == {"demo-fix-3-1"}
    assert set(devin.archived) == triage_ids
    assert not fix_ids & set(devin.archived)
    assert all(row.archived for row in deps.db.list_sessions(stage=Stage.TRIAGE))
    assert deps.db.get_issue(3).package and deps.db.get_issue(3).bump_kind is not None


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


async def test_triage_that_opens_a_pr_is_rejected(tmp_path):
    deps = await make_deps(tmp_path)
    actions = RecordingActions()
    seed(deps, 9001)
    await run_ticks(deps, actions, ticks=4)
    assert state(deps, 9001) is IssueState.NEEDS_HUMAN
    routes = [call for call in actions.calls if call[0] == "apply_route" and call[1] == 9001]
    assert len(routes) == 1
    _, _, decision, result, rejected = routes[0]
    assert rejected == ("https://demo.invalid/dmonroym0/superset/pull/9001",)
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


async def test_register_without_issue_actions_falls_back_to_noop(tmp_path):
    if importlib.util.find_spec("app.issue_actions") is not None:
        pytest.skip("app.issue_actions is present")
    deps = await make_deps(tmp_path)
    app = FastAPI()
    register(app, deps)
    assert isinstance(app.state.issue_actions, NoopIssueActions)
