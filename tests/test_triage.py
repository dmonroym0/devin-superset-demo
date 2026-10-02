import pytest
from test_pipeline import RecordingActions, make_deps, seed, state

from app.fake_devin import FakeDevin
from app.models import Confidence, IssueState, PullRequestRef, SessionInfo, Stage, Verdict
from app.triage import TriageParseError, check_triage, parse_triage_output, start_triage

OUTPUT = {
    "issue_number": 1,
    "package": "foo",
    "installed_version": "1.0",
    "head_sha": "abc",
    "comment_url": "",
    "caveats": ["c"],
    "cves": [
        {
            "cve_id": "CVE-2026-1",
            "verdict": "REACHABLE",
            "confidence": "medium",
            "evidence": [{"file": "a.py", "line": 3, "description": "calls foo"}],
            "notes": "n",
        },
        {"cve_id": "CVE-2026-2", "verdict": "UNKNOWN", "confidence": "sure", "evidence": []},
    ],
}


class ScriptedDevin(FakeDevin):
    def __init__(self):
        super().__init__({})
        self.script: list[SessionInfo] = []
        self.fail_create = False

    async def create_session(self, request):
        if self.fail_create:
            self.requests.append(request)
            raise RuntimeError("upstream unavailable")
        return await super().create_session(request)

    async def get_session(self, session_id):
        return self.script.pop(0)


def test_parse_triage_output_maps_fields():
    result = parse_triage_output(OUTPUT, 1)
    first, second = result.cves
    assert first.verdict is Verdict.REACHABLE and first.confidence is Confidence.MEDIUM
    assert first.evidence == ("a.py:3 — calls foo",)
    assert second.confidence is None
    assert result.package == "foo" and result.caveats == ("c",)


@pytest.mark.parametrize(
    "output",
    [None, {}, {"cves": []}, {"cves": [{"cve_id": "CVE-1", "verdict": "MAYBE"}]}, {"cves": "x"}],
)
def test_parse_triage_output_rejects_invalid(output):
    with pytest.raises(TriageParseError):
        parse_triage_output(output, 1)


async def test_start_triage_creates_session_and_marks_in_progress(tmp_path):
    deps = await make_deps(tmp_path)
    actions = RecordingActions()
    seed(deps, 1)
    await start_triage(deps, deps.db.get_issue(1), actions)
    assert state(deps, 1) is IssueState.TRIAGING
    request = deps.devin.requests[0]
    assert request.title == "triage dmonroym0/superset#1"
    assert request.tags == ("devin-superset-demo", "issue-1", "stage-triage")
    assert actions.names(1) == ["mark_in_progress"]
    row = deps.db.list_sessions(stage=Stage.TRIAGE, issue_number=1)[0]
    assert row.max_acu_limit == 5 and row.devin_mode is None
    assert deps.budget.committed() == 5


async def test_start_triage_cas_loss_does_nothing(tmp_path):
    deps = await make_deps(tmp_path)
    seed(deps, 1)
    stale = deps.db.get_issue(1)
    deps.db.transition(1, [IssueState.SEEN], IssueState.TRIAGING, deps.clock())
    await start_triage(deps, stale, RecordingActions())
    assert deps.devin.requests == [] and deps.budget.committed() == 0


async def test_create_failure_cancels_reservation_and_errors(tmp_path):
    devin = ScriptedDevin()
    deps = await make_deps(tmp_path, devin=devin, DEVIN_API_KEY="sk-secret-value")
    devin.fail_create = True
    seed(deps, 1)
    await start_triage(deps, deps.db.get_issue(1), RecordingActions())
    row = deps.db.get_issue(1)
    assert row.state is IssueState.ERROR
    assert row.last_error.startswith("RuntimeError") and "sk-secret-value" not in row.last_error
    assert deps.budget.committed() == 0


async def test_queued_budget_marked_once(tmp_path):
    deps = await make_deps(tmp_path, ACU_CEILING="5", TRIAGE_ACU_CAP="5", FIX_ACU_CAP="5")
    actions = RecordingActions()
    seed(deps, 1, 2)
    await start_triage(deps, deps.db.get_issue(1), actions)
    for _ in range(3):
        await start_triage(deps, deps.db.get_issue(2), actions)
    assert state(deps, 2) is IssueState.QUEUED_BUDGET
    assert actions.names(2) == ["mark_queued_budget"]


async def test_pr_on_an_unsettled_poll_rejects_triage(tmp_path):
    devin = ScriptedDevin()
    deps = await make_deps(tmp_path, devin=devin)
    actions = RecordingActions()
    seed(deps, 1)
    await start_triage(deps, deps.db.get_issue(1), actions)
    session = deps.db.list_sessions(stage=Stage.TRIAGE)[0]
    url = "https://github.com/dmonroym0/superset/pull/1"
    devin.script.append(
        SessionInfo(
            session.session_id,
            "running",
            status_detail="working",
            pull_requests=(PullRequestRef(url),),
            structured_output=OUTPUT,
        )
    )
    await check_triage(deps, session, actions)
    assert state(deps, 1) is IssueState.NEEDS_HUMAN
    route_call = next(c for c in actions.calls if c[0] == "apply_route")
    assert route_call[3] is None and route_call[4] == (url,)
    assert devin.archived == [session.session_id]


async def test_settled_valid_output_is_triaged_and_archived(tmp_path):
    devin = ScriptedDevin()
    deps = await make_deps(tmp_path, devin=devin)
    seed(deps, 1)
    await start_triage(deps, deps.db.get_issue(1), RecordingActions())
    session = deps.db.list_sessions(stage=Stage.TRIAGE)[0]
    devin.script.append(
        SessionInfo(
            session.session_id,
            "running",
            status_detail="finished",
            structured_output=OUTPUT,
            acus_consumed=1.5,
        )
    )
    await check_triage(deps, session, RecordingActions())
    assert state(deps, 1) is IssueState.TRIAGED
    stored = deps.db.get_session(session.session_id)
    assert stored.structured_output == OUTPUT and stored.acus_consumed == 1.5 and stored.archived


@pytest.mark.parametrize(
    "info",
    [
        SessionInfo("x", "exit", structured_output={"cves": []}),
        SessionInfo("x", "error", structured_output=OUTPUT),
    ],
)
async def test_unusable_output_is_needs_human(tmp_path, info):
    devin = ScriptedDevin()
    deps = await make_deps(tmp_path, devin=devin)
    actions = RecordingActions()
    seed(deps, 1)
    await start_triage(deps, deps.db.get_issue(1), actions)
    session = deps.db.list_sessions(stage=Stage.TRIAGE)[0]
    devin.script.append(info)
    await check_triage(deps, session, actions)
    assert state(deps, 1) is IssueState.NEEDS_HUMAN
    assert "mark_needs_human" in actions.names(1)
