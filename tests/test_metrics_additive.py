from app.budget import Budget
from app.config import Settings
from app.db import Database, SessionRow
from app.metrics import compute
from app.models import BumpKind, Issue, IssueState, Stage


def _make_db():
    db = Database(":memory:")
    db.init_schema()
    settings = Settings.from_env({})
    budget = Budget(db, ceiling=120)
    return db, budget, settings


def _add_issue(db, number, first_seen=10):
    db.upsert_seen_issue(Issue(number=number, title=f"Issue {number}", body=""), first_seen)


def _add_session(
    db,
    session_id,
    issue_number,
    stage,
    created_at,
    settled_at=None,
    structured_output=None,
    archived=False,
):
    db.insert_session(
        SessionRow(
            session_id=session_id,
            issue_number=issue_number,
            stage=stage,
            status="running" if settled_at is None else "exit",
            status_detail=None if settled_at is None else "finished",
            devin_mode="interactive",
            max_acu_limit=5 if stage is Stage.TRIAGE else 15,
            acus_consumed=1.5,
            url=f"https://example.test/sessions/{session_id}",
            created_at=created_at,
            updated_at=created_at if settled_at is None else settled_at,
            settled_at=settled_at,
            archived=archived,
            structured_output=structured_output,
        )
    )


def _triage_output(verdict="REACHABLE"):
    return {
        "cves": [
            {
                "cve_id": "CVE-2026-1234",
                "verdict": verdict,
                "confidence": "high",
                "evidence": [
                    {"file": "src/example.py", "line": 12, "description": "reachable call"},
                ],
                "gating": {
                    "feature_flags": ["FLAG_X"],
                    "config_options": ["option_x"],
                    "required_permissions": ["admin"],
                },
                "reachable_by_default": True,
                "notes": "reviewed",
            }
        ],
        "caveats": ["scripted result"],
    }


def _triaged(db, number, at=30, bump_kind=BumpKind.PATCH):
    assert db.transition(
        number,
        [IssueState.TRIAGING],
        IssueState.TRIAGED,
        at,
        triaged_at=at,
        package="example",
        current_version="1.0.0",
        fixed_version="1.0.1",
        bump_kind=bump_kind,
    )


def test_pr_opened_has_complete_stage_track_and_durations():
    db, budget, settings = _make_db()
    _add_issue(db, 1)
    assert db.transition(1, [IssueState.SEEN], IssueState.TRIAGING, 20)
    _add_session(db, "triage-1", 1, Stage.TRIAGE, 20, 30, _triage_output())
    _triaged(db, 1)
    db.add_event(1, "routed", "fix: patch is available", 31)
    assert db.transition(1, [IssueState.TRIAGED], IssueState.FIXING, 32)
    _add_session(db, "fix-1", 1, Stage.FIX, 32, 49)
    assert db.transition(
        1,
        [IssueState.FIXING],
        IssueState.PR_OPENED,
        50,
        pr_url="https://github.com/example/repo/pull/1",
        pr_opened_at=50,
    )

    issue = compute(db, budget, settings, now=60)["issues_detail"][0]
    assert [stage["state"] for stage in issue["stage_track"]] == ["done"] * 5
    assert [stage["key"] for stage in issue["stage_track"]] == [
        "seen",
        "triage",
        "route",
        "fix",
        "pr",
    ]
    assert [stage["duration_s"] for stage in issue["stage_track"]] == [10, 10, None, 17, 0]
    assert issue["session_ids"] == {"triage": "triage-1", "fix": "fix-1"}
    assert issue["route"] == {
        "action": "fix",
        "reason": "patch is available",
        "at": "1970-01-01T00:00:31+00:00",
    }
    db.close()


def test_not_reachable_route_skips_fix_and_pr_and_keeps_cves():
    db, budget, settings = _make_db()
    _add_issue(db, 2)
    assert db.transition(2, [IssueState.SEEN], IssueState.TRIAGING, 20)
    _add_session(db, "triage-2", 2, Stage.TRIAGE, 20, 30, _triage_output("NOT_REACHABLE"))
    _triaged(db, 2)
    db.add_event(2, "routed", "close_low_priority: not reachable", 31)
    assert db.transition(2, [IssueState.TRIAGED], IssueState.NOT_REACHABLE, 31)

    issue = compute(db, budget, settings, now=40)["issues_detail"][0]
    assert [stage["state"] for stage in issue["stage_track"]] == [
        "done",
        "done",
        "done",
        "skipped",
        "skipped",
    ]
    assert {cve["verdict"] for cve in issue["cves"]} == {"NOT_REACHABLE"}
    assert issue["cves"][0]["gating"]["feature_flags"] == ["FLAG_X"]
    assert issue["cves"][0]["reachable_by_default"] is True
    db.close()


def test_needs_human_at_route_stops_route_and_skips_fix_and_pr():
    db, budget, settings = _make_db()
    _add_issue(db, 3)
    assert db.transition(3, [IssueState.SEEN], IssueState.TRIAGING, 20)
    _add_session(db, "triage-3", 3, Stage.TRIAGE, 20, 30, _triage_output())
    _triaged(db, 3, bump_kind=BumpKind.MAJOR)
    db.add_event(3, "routed", "needs_human: major version bump", 31)
    assert db.transition(3, [IssueState.TRIAGED], IssueState.NEEDS_HUMAN, 31)

    issue = compute(db, budget, settings, now=40)["issues_detail"][0]
    assert issue["bump_kind"] == "major"
    assert [stage["state"] for stage in issue["stage_track"]] == [
        "done",
        "done",
        "stopped",
        "skipped",
        "skipped",
    ]
    db.close()


def test_needs_human_after_fix_stops_fix_and_skips_pr():
    db, budget, settings = _make_db()
    _add_issue(db, 4)
    assert db.transition(4, [IssueState.SEEN], IssueState.TRIAGING, 20)
    _add_session(db, "triage-4", 4, Stage.TRIAGE, 20, 30, _triage_output())
    _triaged(db, 4)
    db.add_event(4, "routed", "fix: eligible", 31)
    assert db.transition(4, [IssueState.TRIAGED], IssueState.FIXING, 32)
    _add_session(db, "fix-4", 4, Stage.FIX, 32, 39)
    assert db.transition(4, [IssueState.FIXING], IssueState.NEEDS_HUMAN, 40)

    issue = compute(db, budget, settings, now=50)["issues_detail"][0]
    assert [stage["state"] for stage in issue["stage_track"]] == [
        "done",
        "done",
        "done",
        "stopped",
        "skipped",
    ]
    db.close()


def test_queued_budget_before_triage_is_waiting():
    db, budget, settings = _make_db()
    _add_issue(db, 5)
    assert db.transition(5, [IssueState.SEEN], IssueState.QUEUED_BUDGET, 11)

    issue = compute(db, budget, settings, now=20)["issues_detail"][0]
    assert issue["stage_track"][1]["state"] == "waiting"
    db.close()


def test_active_triage_duration_runs_through_now():
    db, budget, settings = _make_db()
    _add_issue(db, 6)
    assert db.transition(6, [IssueState.SEEN], IssueState.TRIAGING, 12)
    _add_session(db, "triage-6", 6, Stage.TRIAGE, 12)

    issue = compute(db, budget, settings, now=25)["issues_detail"][0]
    assert issue["stage_track"][1]["state"] == "active"
    assert issue["stage_track"][1]["duration_s"] == 13
    db.close()


def test_error_during_fix_marks_fix_failed():
    db, budget, settings = _make_db()
    _add_issue(db, 7)
    assert db.transition(7, [IssueState.SEEN], IssueState.TRIAGING, 20)
    _add_session(db, "triage-7", 7, Stage.TRIAGE, 20, 30, _triage_output())
    _triaged(db, 7)
    db.add_event(7, "routed", "fix: eligible", 31)
    assert db.transition(7, [IssueState.TRIAGED], IssueState.FIXING, 32)
    _add_session(db, "fix-7", 7, Stage.FIX, 32, 38)
    assert db.transition(7, [IssueState.FIXING], IssueState.ERROR, 39, last_error="fix failed")

    issue = compute(db, budget, settings, now=40)["issues_detail"][0]
    assert issue["stage_track"][3]["state"] == "failed"
    db.close()


def test_malformed_structured_output_is_normalized_without_raising():
    db, budget, settings = _make_db()
    outputs = [
        None,
        {"cves": "x", "caveats": ["kept caveat"]},
        {"cves": [{"verdict": "REACHABLE"}]},
        {
            "cves": [
                {
                    "cve_id": "CVE-2026-1",
                    "verdict": "NOT_REACHABLE",
                    "evidence": ["plain evidence"],
                }
            ]
        },
        {
            "cves": [
                {
                    "cve_id": "CVE-2026-2",
                    "verdict": "invalid",
                    "confidence": "invalid",
                    "evidence": [{"file": 3, "line": True, "description": None}],
                    "gating": {"feature_flags": "not-a-list"},
                    "reachable_by_default": "yes",
                }
            ]
        },
    ]
    for number, output in enumerate(outputs, start=10):
        _add_issue(db, number)
        assert db.transition(number, [IssueState.SEEN], IssueState.TRIAGING, 11)
        _add_session(db, f"triage-{number}", number, Stage.TRIAGE, 11, 12, output)

    issues = compute(db, budget, settings, now=20)["issues_detail"]
    assert [issue["cves"] for issue in issues[:3]] == [[], [], []]
    assert issues[1]["caveats"] == ["kept caveat"]
    assert issues[3]["cves"] == [
        {
            "cve_id": "CVE-2026-1",
            "verdict": "NOT_REACHABLE",
            "confidence": None,
            "evidence": [{"file": None, "line": None, "description": "plain evidence"}],
            "gating": {
                "feature_flags": [],
                "config_options": [],
                "required_permissions": [],
            },
            "reachable_by_default": None,
            "notes": "",
        }
    ]
    assert issues[4]["cves"][0]["verdict"] == "UNKNOWN"
    assert issues[4]["cves"][0]["confidence"] is None
    assert issues[4]["cves"][0]["evidence"] == [{"file": None, "line": None, "description": ""}]
    assert issues[4]["cves"][0]["reachable_by_default"] is None
    db.close()


def test_acu_reservations_and_by_issue_include_cancelled_rows():
    db, budget, settings = _make_db()
    first = budget.reserve(9, Stage.TRIAGE, 20, 1)
    second = budget.reserve(2, Stage.FIX, 15, 2)
    cancelled = budget.reserve(9, Stage.FIX, 10, 3)
    assert first and second and cancelled
    assert budget.attach(first, "triage-9")
    assert budget.cancel(cancelled)

    acu = compute(db, budget, settings, now=10)["acu"]
    assert acu["reservations"] == [
        {
            "issue_number": 9,
            "stage": "triage",
            "cap": 20,
            "created_at": "1970-01-01T00:00:01+00:00",
            "cancelled": False,
            "session_id": "triage-9",
        },
        {
            "issue_number": 2,
            "stage": "fix",
            "cap": 15,
            "created_at": "1970-01-01T00:00:02+00:00",
            "cancelled": False,
            "session_id": None,
        },
        {
            "issue_number": 9,
            "stage": "fix",
            "cap": 10,
            "created_at": "1970-01-01T00:00:03+00:00",
            "cancelled": True,
            "session_id": None,
        },
    ]
    assert acu["by_issue"] == [
        {"issue_number": 2, "committed": 15},
        {"issue_number": 9, "committed": 20},
    ]
    db.close()


def test_session_duration_finished_and_archived_fields():
    db, budget, settings = _make_db()
    _add_session(db, "settled", 100, Stage.TRIAGE, 10, 14, archived=True)
    _add_session(db, "active", 101, Stage.FIX, 20)

    sessions = compute(db, budget, settings, now=30)["sessions"]
    assert sessions[0]["duration_s"] == 4
    assert sessions[0]["finished"] is True
    assert sessions[0]["archived"] is True
    assert sessions[1]["duration_s"] == 10
    assert sessions[1]["finished"] is False
    assert sessions[1]["settled_at"] is None
    db.close()


def test_all_preexisting_metrics_keys_keep_their_values():
    db, budget, settings = _make_db()
    _add_issue(db, 42, first_seen=1)
    assert db.transition(
        42,
        [IssueState.SEEN],
        IssueState.PR_OPENED,
        3,
        pr_url="https://github.com/example/repo/pull/42",
        pr_opened_at=3,
        route_reason="fix",
    )
    _add_session(db, "legacy-session", 42, Stage.TRIAGE, 1, 2)

    metrics = compute(db, budget, settings, now=10)
    assert {
        "generated_at",
        "mode",
        "issues",
        "automation_rate",
        "median_time_to_pr_s",
        "acu",
        "upstream_sync",
        "sessions",
        "issues_detail",
    } <= metrics.keys()
    assert metrics["generated_at"] == "1970-01-01T00:00:10+00:00"
    assert metrics["mode"] == settings.mode.value
    assert metrics["issues"] == {
        "seen": 1,
        "in_flight": 0,
        "pr_opened": 1,
        "needs_human": 0,
        "not_reachable": 0,
        "queued_budget": 0,
        "error": 0,
        "cancelled": 0,
    }
    assert metrics["automation_rate"] == 1.0
    assert metrics["median_time_to_pr_s"] == 2
    assert {
        "committed",
        "ceiling",
        "remaining",
        "consumed_metered",
    } <= metrics["acu"].keys()
    assert metrics["acu"] == {
        "committed": 0,
        "ceiling": 120,
        "remaining": 120,
        "consumed_metered": 1.5,
        "reservations": [],
        "by_issue": [],
    }
    assert metrics["upstream_sync"] == {
        "enabled": settings.upstream_sync_enabled,
        "last_outcome": None,
        "last_at": None,
        "changelog_pr_url": None,
        "conflict_issue_number": None,
        "changelog_through_sha": None,
    }
    session = metrics["sessions"][0]
    old_session_keys = {
        "session_id",
        "issue_number",
        "stage",
        "status",
        "status_detail",
        "devin_mode",
        "max_acu_limit",
        "acus_consumed",
        "url",
        "created_at",
    }
    assert old_session_keys <= session.keys()
    assert {key: session[key] for key in old_session_keys} == {
        "session_id": "legacy-session",
        "issue_number": 42,
        "stage": "triage",
        "status": "exit",
        "status_detail": "finished",
        "devin_mode": "interactive",
        "max_acu_limit": 5,
        "acus_consumed": 1.5,
        "url": "https://example.test/sessions/legacy-session",
        "created_at": "1970-01-01T00:00:01+00:00",
    }
    issue = metrics["issues_detail"][0]
    old_issue_keys = {
        "number",
        "title",
        "state",
        "route_reason",
        "pr_url",
        "first_seen_at",
        "pr_opened_at",
    }
    assert old_issue_keys <= issue.keys()
    assert {key: issue[key] for key in old_issue_keys} == {
        "number": 42,
        "title": "Issue 42",
        "state": "pr_opened",
        "route_reason": "fix",
        "pr_url": "https://github.com/example/repo/pull/42",
        "first_seen_at": "1970-01-01T00:00:01+00:00",
        "pr_opened_at": "1970-01-01T00:00:03+00:00",
    }
    db.close()


def test_unsettled_terminal_sessions_have_terminal_stage_states_and_no_live_duration():
    db, budget, settings = _make_db()

    _add_issue(db, 50)
    assert db.transition(50, [IssueState.SEEN], IssueState.TRIAGING, 20)
    _add_session(db, "triage-error", 50, Stage.TRIAGE, 20)
    assert db.transition(50, [IssueState.TRIAGING], IssueState.ERROR, 25, last_error="triage failed")

    _add_issue(db, 51)
    assert db.transition(51, [IssueState.SEEN], IssueState.TRIAGING, 20)
    _add_session(db, "triage-human", 51, Stage.TRIAGE, 20)
    assert db.transition(51, [IssueState.TRIAGING], IssueState.NEEDS_HUMAN, 25)

    _add_issue(db, 52)
    assert db.transition(52, [IssueState.SEEN], IssueState.TRIAGING, 20)
    _add_session(db, "triage-fix-error", 52, Stage.TRIAGE, 20, 30, _triage_output())
    _triaged(db, 52)
    db.add_event(52, "routed", "fix: eligible", 31)
    assert db.transition(52, [IssueState.TRIAGED], IssueState.FIXING, 32)
    _add_session(db, "fix-error", 52, Stage.FIX, 32)
    assert db.transition(52, [IssueState.FIXING], IssueState.ERROR, 40, last_error="fix failed")

    issues = {issue["number"]: issue for issue in compute(db, budget, settings, now=100)["issues_detail"]}
    assert issues[50]["stage_track"][1]["state"] == "failed"
    assert issues[50]["stage_track"][1]["duration_s"] is None
    assert issues[51]["stage_track"][1]["state"] == "stopped"
    assert issues[51]["stage_track"][1]["duration_s"] is None
    assert issues[52]["stage_track"][3]["state"] == "failed"
    assert issues[52]["stage_track"][3]["duration_s"] is None
    db.close()


def test_relabelled_cancelled_issue_discards_the_previous_run():
    db, budget, settings = _make_db()
    _add_issue(db, 53)
    assert db.transition(53, [IssueState.SEEN], IssueState.TRIAGING, 20)
    _add_session(db, "old-triage", 53, Stage.TRIAGE, 20, 30, _triage_output())
    _triaged(db, 53, at=30)
    db.add_event(53, "routed", "fix: previous run", 31)
    assert db.transition(53, [IssueState.TRIAGED], IssueState.FIXING, 32)
    _add_session(db, "old-fix", 53, Stage.FIX, 32, 35)
    assert db.transition(53, [IssueState.FIXING], IssueState.CANCELLED, 36)

    accepted, is_new = db.accept_delivery(
        "relabel-cancelled-53",
        "issues",
        "labeled",
        Issue(number=53, title="Relabeled issue", body=""),
        40,
    )
    assert accepted and is_new

    issue = compute(db, budget, settings, now=45)["issues_detail"][0]
    assert issue["route"] == {"action": None, "reason": None, "at": None}
    assert issue["session_ids"] == {"triage": None, "fix": None}
    assert issue["cves"] == []
    assert issue["stage_track"][1]["state"] == "pending"
    assert issue["stage_track"][2]["state"] == "pending"

    assert db.transition(53, [IssueState.SEEN], IssueState.TRIAGING, 50)
    _add_session(db, "new-triage", 53, Stage.TRIAGE, 50, structured_output=_triage_output())
    issue = compute(db, budget, settings, now=51)["issues_detail"][0]
    assert issue["session_ids"] == {"triage": "new-triage", "fix": None}
    assert issue["route"] == {"action": None, "reason": None, "at": None}
    assert issue["stage_track"][2]["state"] == "pending"

    db.update_session("new-triage", settled_at=55, updated_at=55)
    assert db.transition(53, [IssueState.TRIAGING], IssueState.NEEDS_HUMAN, 56)
    issue = compute(db, budget, settings, now=60)["issues_detail"][0]
    assert issue["route"] == {"action": None, "reason": None, "at": None}
    assert issue["session_ids"]["fix"] is None
    assert issue["stage_track"][1]["state"] == "stopped"
    assert issue["stage_track"][2]["state"] == "skipped"
    db.close()
