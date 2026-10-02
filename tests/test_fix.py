from test_pipeline import RecordingActions, make_deps, run_ticks, seed, state
from test_triage import ScriptedDevin

from app.fix import check_fix
from app.models import IssueState, SessionInfo, Stage


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


async def test_fix_budget_refusal_keeps_triaged(tmp_path):
    deps = await make_deps(tmp_path, ACU_CEILING="15")
    actions = RecordingActions()
    seed(deps, 1)
    await run_ticks(deps, actions, ticks=5)
    assert state(deps, 1) is IssueState.TRIAGED
    assert actions.names(1).count("mark_queued_budget") == 1
    assert not [r for r in deps.devin.requests if "stage-fix" in r.tags]
