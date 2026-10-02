import sqlite3
from dataclasses import replace

import pytest
from test_pipeline import (
    RecordingActions,
    make_deps,
    record_create_then_timeout,
    run_ticks,
    seed,
    state,
)
from test_triage import ScriptedDevin

from app.fake_devin import FakeDevin
from app.fix import check_fix, start_fix
from app.models import (
    IssueState,
    PullRequestRef,
    RouteAction,
    RouteDecision,
    SessionInfo,
    Stage,
    TriageResult,
)
from app.pipeline import _recover_claims_after_restart, tick


async def advance_to_fixing(tmp_path, number=1):
    deps = await make_deps(tmp_path)
    actions = RecordingActions()
    seed(deps, number)
    for _ in range(10):
        await run_ticks(deps, actions, ticks=1)
        if state(deps, number) is IssueState.FIXING:
            break
    return deps, actions


async def test_route_triaged_stores_facts_and_starts_fix(tmp_path):
    deps, actions = await advance_to_fixing(tmp_path, 1)
    row = deps.db.get_issue(1)
    assert row.state is IssueState.FIXING
    assert row.package == "jaraco-context"
    assert (row.current_version, row.fixed_version, row.bump_kind.value) == ("6.0.1", "6.1.0", "minor")
    assert row.route_reason.startswith("reachable")
    fix_request = next(r for r in deps.devin.requests if "stage-fix" in r.tags)
    assert fix_request.tags == ("devin-superset-demo", "issue-1", "stage-fix")
    assert fix_request.prompt.startswith("!dep_security_fix")
    assert fix_request.structured_output_schema is None
    assert actions.names(1).count("apply_route") == 1


async def test_fix_pr_opened_and_never_archived(tmp_path):
    deps, actions = await advance_to_fixing(tmp_path, 3)
    await run_ticks(deps, actions, ticks=6)
    assert state(deps, 3) is IssueState.PR_OPENED
    assert deps.db.get_issue(3).pr_opened_at is not None
    assert not [sid for sid in deps.devin.archived if sid.startswith("demo-fix-")]


async def test_fix_without_trigger_label_is_cancelled_before_session(tmp_path):
    deps = await make_deps(tmp_path)
    actions = RecordingActions()
    seed(deps, 1)
    issue = deps.github.issues[1]
    deps.db.transition(1, [IssueState.SEEN], IssueState.TRIAGED, deps.clock())
    deps.db.transition(1, [IssueState.TRIAGED], IssueState.FIXING, deps.clock())
    deps.github.issues[1] = replace(
        issue,
        labels=tuple(label for label in issue.labels if label != deps.settings.trigger_label),
    )

    await start_fix(
        deps,
        issue,
        TriageResult(1, ()),
        RouteDecision(RouteAction.FIX, "reachable"),
        actions,
    )

    row = deps.db.get_issue(1)
    assert row.state is IssueState.CANCELLED
    assert row.route_reason == "trigger label removed"
    assert any(event.kind == "cancelled_before_session" for event in deps.db.list_events(1))
    assert deps.budget.committed() == 0
    assert deps.devin.requests == []


async def test_ambiguous_fix_create_keeps_reservation_and_does_not_retry(tmp_path):
    devin = FakeDevin.from_scenarios()
    created = record_create_then_timeout(devin, stages=("fix",))
    deps = await make_deps(tmp_path, devin=devin)
    actions = RecordingActions()
    seed(deps, 1)

    await run_ticks(deps, actions, ticks=12)

    row = deps.db.get_issue(1)
    assert row.state is IssueState.NEEDS_HUMAN
    assert row.route_reason == "session creation outcome unknown; ACU reservation kept until reviewed"
    assert deps.budget.committed() == deps.settings.triage_acu_cap + deps.settings.fix_acu_cap
    assert len(created) == 1
    assert any(event.kind == "fix_create_ambiguous" for event in deps.db.list_events(1))
    assert actions.names(1).count("mark_needs_human") == 1
    await run_ticks(deps, actions, ticks=3)
    assert len(created) == 1


async def test_settled_without_pr_is_needs_human(tmp_path):
    deps, actions = await advance_to_fixing(tmp_path, 1)
    session = deps.db.list_sessions(stage=Stage.FIX, issue_number=1)[0]
    scripted = ScriptedDevin()
    scripted.script.append(SessionInfo(session.session_id, "exit", status_detail="finished"))
    deps.devin = scripted
    await check_fix(deps, session, actions)
    row = deps.db.get_issue(1)
    assert row.state is IssueState.NEEDS_HUMAN
    assert row.route_reason == "fix session finished without a PR"
    assert ("mark_needs_human", 1, "fix session finished without a PR") in actions.calls
    assert scripted.archived == []


@pytest.mark.parametrize(
    ("status", "status_detail", "reason"),
    [
        ("suspended", "waiting for approval", "fix session suspended (waiting for approval)"),
        ("error", None, "fix session errored (no detail)"),
    ],
)
async def test_settled_fix_preserves_suspended_and_error_reasons(tmp_path, status, status_detail, reason):
    deps, actions = await advance_to_fixing(tmp_path, 1)
    session = deps.db.list_sessions(stage=Stage.FIX, issue_number=1)[0]
    scripted = ScriptedDevin()
    scripted.script.append(SessionInfo(session.session_id, status, status_detail=status_detail))
    deps.devin = scripted

    await check_fix(deps, session, actions)

    assert deps.db.get_issue(1).route_reason == reason
    assert ("mark_needs_human", 1, reason) in actions.calls


async def test_failed_atomic_pr_transition_keeps_fix_session_active_for_retry(tmp_path):
    deps, actions = await advance_to_fixing(tmp_path, 1)
    session = deps.db.list_sessions(stage=Stage.FIX, issue_number=1)[0]
    pr_result = SessionInfo(
        session.session_id,
        "exit",
        status_detail="finished",
        pull_requests=(PullRequestRef("https://github.com/dmonroym0/superset/pull/42"),),
    )
    poll_results = [pr_result, pr_result]

    async def get_pr_result(session_id):
        return poll_results.pop(0)

    deps.devin.get_session = get_pr_result
    deps.db._connection.execute(
        "CREATE TRIGGER fail_pr_opened BEFORE UPDATE OF state ON issues "
        "WHEN NEW.state='pr_opened' BEGIN SELECT RAISE(ABORT, 'boom'); END"
    )
    deps.db._connection.commit()

    with pytest.raises(sqlite3.IntegrityError):
        await check_fix(deps, session, actions)

    assert deps.db.get_issue(1).state is IssueState.FIXING
    assert deps.db.get_session(session.session_id).settled_at is None

    deps.db._connection.execute("DROP TRIGGER fail_pr_opened")
    deps.db._connection.commit()
    await _recover_claims_after_restart(deps, actions)
    await tick(deps, actions)

    assert deps.db.get_issue(1).state is IssueState.PR_OPENED
    assert deps.db.get_session(session.session_id).settled_at is not None
    assert len([request for request in deps.devin.requests if "stage-fix" in request.tags]) == 1


async def test_fix_budget_refusal_keeps_triaged(tmp_path):
    deps = await make_deps(tmp_path, ACU_CEILING="15")
    actions = RecordingActions()
    seed(deps, 1)
    await run_ticks(deps, actions, ticks=5)
    assert state(deps, 1) is IssueState.TRIAGED
    assert actions.names(1).count("mark_queued_budget") == 1
    assert not [r for r in deps.devin.requests if "stage-fix" in r.tags]


async def test_fix_budget_comment_retry_after_failure(tmp_path):
    deps = await make_deps(tmp_path, ACU_CEILING="15")
    actions = RecordingActions()
    seed(deps, 1)
    await run_ticks(deps, actions, ticks=5)
    assert actions.names(1).count("mark_queued_budget") == 1

    deps.db.add_event(1, "queued_budget_comment_failed", "x", deps.clock())
    await run_ticks(deps, actions, ticks=1)
    assert actions.names(1).count("mark_queued_budget") == 2

    deps.db.add_event(1, "queued_budget_commented", "x", deps.clock())
    await run_ticks(deps, actions, ticks=1)
    assert actions.names(1).count("mark_queued_budget") == 2
