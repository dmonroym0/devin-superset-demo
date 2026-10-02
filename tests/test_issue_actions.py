import pytest

from app.config import Settings
from app.fake_github import FakeGitHub
from app.github_client import GitHubError
from app.issue_actions import (
    apply_route,
    clear_queued_budget,
    mark_in_progress,
    mark_needs_human,
    mark_pr_opened,
    mark_queued_budget,
    render_triage_comment,
)
from app.main import create_app
from app.models import (
    LABEL_IN_PROGRESS,
    LABEL_LOW_PRIORITY,
    LABEL_NEEDS_HUMAN,
    LABEL_PR_OPENED,
    LABEL_QUEUED_BUDGET,
    LABEL_TRIAGE_REJECTED,
    Confidence,
    CveFinding,
    RouteAction,
    RouteDecision,
    TriageResult,
    Verdict,
)


@pytest.fixture
def action_context(tmp_path, fake_devin):
    github = FakeGitHub.from_seed()
    app = create_app(
        Settings.from_env({"DB_PATH": str(tmp_path / "actions.db")}),
        github=github,
        devin=fake_devin,
        clock=lambda: 100.0,
    )
    deps = app.state.deps
    deps.db.init_schema()
    yield deps, github
    deps.db.close()


@pytest.mark.asyncio
async def test_route_actions_comment_label_and_close(action_context):
    deps, github = action_context
    reachable = CveFinding(
        "CVE-2026-0001",
        Verdict.REACHABLE,
        Confidence.MEDIUM,
        evidence=("requirements/base.txt:10 | direct use",),
    )
    unknown = CveFinding("CVE-2026-0002", Verdict.UNKNOWN)

    await mark_in_progress(deps, 1)
    comment_url = await apply_route(
        deps,
        1,
        RouteDecision(RouteAction.FIX, "reachable", qualifying=(reachable,), others=(unknown,)),
        TriageResult(1, (reachable, unknown)),
    )
    assert comment_url
    assert LABEL_IN_PROGRESS in github.labels(1)
    assert "CVE-2026-0002" in github.comments[1][0]
    assert "UNKNOWN" in github.comments[1][0]
    assert r"requirements/base.txt:10 \| direct use" in github.comments[1][0]

    await mark_in_progress(deps, 2)
    await apply_route(deps, 2, RouteDecision(RouteAction.NEEDS_HUMAN, "check <unsafe>"), None)
    assert LABEL_NEEDS_HUMAN in github.labels(2)
    assert LABEL_IN_PROGRESS not in github.labels(2)
    assert "&lt;unsafe&gt;" in github.comments[2][0]

    await mark_in_progress(deps, 3)
    await apply_route(
        deps,
        3,
        RouteDecision(RouteAction.CLOSE_LOW_PRIORITY, "not reachable", others=(unknown,)),
        None,
    )
    assert LABEL_LOW_PRIORITY in github.labels(3)
    assert LABEL_IN_PROGRESS not in github.labels(3)
    assert github.issues[3].state == "closed"
    assert github.closed[3] == "not_planned"


@pytest.mark.asyncio
async def test_rejected_prs_and_other_issue_actions(action_context):
    deps, github = action_context
    await mark_in_progress(deps, 4)
    await apply_route(
        deps,
        4,
        RouteDecision(RouteAction.NEEDS_HUMAN, "read-only violation"),
        None,
        rejected_pr_urls=("https://demo.invalid/pr/1",),
    )
    assert LABEL_TRIAGE_REJECTED in github.labels(4)
    assert "read-only triage session opened PR(s)" in github.comments[4][0]
    assert "https://demo.invalid/pr/1" in github.comments[4][0]

    await mark_pr_opened(deps, 5, ("https://demo.invalid/pr/2", "https://evil.example/pr/3(extra)"))
    assert LABEL_PR_OPENED in github.labels(5)
    assert LABEL_IN_PROGRESS not in github.labels(5)
    assert "- <https://demo.invalid/pr/2>" in github.comments[5][0]
    assert "- (unsafe URL omitted)" in github.comments[5][0]
    assert "evil.example" not in github.comments[5][0]
    assert "Opened, not done: CI and review continue in the Devin session." in github.comments[5][0]

    await mark_needs_human(deps, 1, "human | decision")
    assert LABEL_NEEDS_HUMAN in github.labels(1)
    assert "human \\| decision" in github.comments[1][-1]


def test_triage_comment_omits_unsafe_urls():
    body = render_triage_comment(
        RouteDecision(RouteAction.FIX, "reachable"),
        TriageResult(1, (), comment_url="https://example.com/x](https://evil.example)"),
        rejected_pr_urls=(
            "https://evil.example/pull/8(extra)",
            "https://github.com/dmonroym0/superset/pull/9",
        ),
    )
    assert "evil.example" not in body
    assert "[full triage comment]" not in body
    assert "- (unsafe URL omitted)" in body
    assert "- <https://github.com/dmonroym0/superset/pull/9>" in body


@pytest.mark.asyncio
async def test_github_errors_are_recorded_and_remaining_actions_continue(action_context):
    deps, github = action_context

    async def failing_comment(number, body):
        raise GitHubError(503, "POST", f"/repos/dmonroym0/superset/issues/{number}/comments")

    github.create_comment = failing_comment
    await mark_needs_human(deps, 2, "needs review")
    assert LABEL_NEEDS_HUMAN in github.labels(2)
    events = deps.db.list_events(2)
    assert any(event.kind == "github_error" and event.detail.endswith("503") for event in events)


@pytest.mark.asyncio
async def test_queued_budget_comment_is_idempotent(action_context):
    deps, github = action_context
    await mark_queued_budget(deps, 1, committed=120, ceiling=120)
    await mark_queued_budget(deps, 1, committed=120, ceiling=120)
    assert LABEL_QUEUED_BUDGET in github.labels(1)
    assert len(github.comments[1]) == 1
    assert "committed 120 / ceiling 120 ACUs" in github.comments[1][0]
    assert sum(event.kind == "queued_budget_commented" for event in deps.db.list_events(1)) == 1
    await clear_queued_budget(deps, 1)
    assert LABEL_QUEUED_BUDGET not in github.labels(1)
    await mark_queued_budget(deps, 1, committed=120, ceiling=120)
    assert LABEL_QUEUED_BUDGET in github.labels(1)
    assert len(github.comments[1]) == 2


@pytest.mark.asyncio
async def test_queued_budget_comment_retries_after_github_error(action_context, monkeypatch):
    deps, github = action_context
    original_create_comment = github.create_comment
    attempts = 0

    async def fail_once(number, body):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise GitHubError(503, "POST", f"/repos/dmonroym0/superset/issues/{number}/comments")
        return await original_create_comment(number, body)

    monkeypatch.setattr(github, "create_comment", fail_once)
    await mark_queued_budget(deps, 1, committed=120, ceiling=120)
    assert github.comments.get(1, []) == []
    assert not any(event.kind == "queued_budget_commented" for event in deps.db.list_events(1))

    await mark_queued_budget(deps, 1, committed=120, ceiling=120)
    assert attempts == 2
    assert len(github.comments[1]) == 1
    assert any(event.kind == "queued_budget_commented" for event in deps.db.list_events(1))
