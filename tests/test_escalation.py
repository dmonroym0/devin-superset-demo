import pytest
from test_fix import advance_to_fixing
from test_pipeline import RecordingActions, make_deps, seed, state

from app.escalation import NUDGE_MESSAGE, check_stuck
from app.models import IssueState, SessionInfo, Stage
from app.triage import start_triage


async def triaging(tmp_path, **env):
    deps = await make_deps(tmp_path, SOFT_TIMEOUT_S="10", HARD_TIMEOUT_S="30", **env)
    seed(deps, 1)
    await start_triage(deps, deps.db.get_issue(1), RecordingActions())
    return deps, deps.db.list_sessions(stage=Stage.TRIAGE)[0]


async def test_soft_timeout_nudges_once(tmp_path):
    deps, session = await triaging(tmp_path)
    actions = RecordingActions()
    info = SessionInfo(session.session_id, "running", status_detail="working")
    deps.clock.now += 5
    assert not await check_stuck(deps, session, info, actions)
    assert deps.devin.messages == []
    deps.clock.now += 6
    assert not await check_stuck(deps, session, info, actions)
    assert not await check_stuck(deps, deps.db.get_session(session.session_id), info, actions)
    assert deps.devin.messages == [(session.session_id, NUDGE_MESSAGE)]
    assert state(deps, 1) is IssueState.TRIAGING


async def test_hard_timeout_escalates(tmp_path):
    deps, session = await triaging(tmp_path)
    actions = RecordingActions()
    deps.clock.now += 30
    info = SessionInfo(session.session_id, "running", status_detail="working")
    assert await check_stuck(deps, session, info, actions)
    assert state(deps, 1) is IssueState.NEEDS_HUMAN
    assert actions.names(1) == ["mark_needs_human"]
    assert deps.db.get_session(session.session_id).settled_at is not None


@pytest.mark.parametrize(
    "info",
    [
        SessionInfo("x", "suspended", status_detail="usage_limit_exceeded"),
        SessionInfo("x", "suspended", status_detail="out_of_quota"),
        SessionInfo("x", "error"),
    ],
)
async def test_limit_reasons_escalate_immediately(tmp_path, info):
    deps, session = await triaging(tmp_path)
    assert await check_stuck(deps, session, info, RecordingActions())
    assert state(deps, 1) is IssueState.NEEDS_HUMAN


async def test_escalated_fix_session_is_not_archived(tmp_path):
    deps, actions = await advance_to_fixing(tmp_path, 1)
    session = deps.db.list_sessions(stage=Stage.FIX, issue_number=1)[0]
    deps.clock.now += deps.settings.hard_timeout_s
    info = SessionInfo(session.session_id, "running", status_detail="working")
    assert await check_stuck(deps, session, info, actions)
    assert state(deps, 1) is IssueState.NEEDS_HUMAN
    assert session.session_id not in deps.devin.archived
