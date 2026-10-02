"""Metrics snapshots for the dashboard and API."""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import UTC, datetime

from app.budget import Budget
from app.config import Settings
from app.db import Database, EventRow, IssueRow, SessionRow
from app.models import Confidence, IssueState, RouteAction, Stage, Verdict
from app.upstream_sync import branch_meta_key

_IN_FLIGHT = {
    IssueState.SEEN,
    IssueState.QUEUED_BUDGET,
    IssueState.TRIAGING,
    IssueState.TRIAGED,
    IssueState.FIXING,
}


def _iso_timestamp(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, UTC).isoformat()


def _session_detail(row: SessionRow, now: float) -> dict:
    return {
        "session_id": row.session_id,
        "issue_number": row.issue_number,
        "stage": row.stage.value,
        "status": row.status,
        "status_detail": row.status_detail,
        "devin_mode": row.devin_mode,
        "max_acu_limit": row.max_acu_limit,
        "acus_consumed": row.acus_consumed,
        "url": row.url,
        "created_at": _iso_timestamp(row.created_at),
        "settled_at": _iso_timestamp(row.settled_at),
        "updated_at": _iso_timestamp(row.updated_at),
        "finished": row.settled_at is not None,
        "archived": row.archived,
        "duration_s": max(0.0, (row.settled_at if row.settled_at is not None else now) - row.created_at),
    }


def _issue_detail(
    row: IssueRow,
    triage_session: SessionRow | None,
    fix_session: SessionRow | None,
    route: dict,
    now: float,
) -> dict:
    detail = {
        "number": row.number,
        "title": row.title,
        "state": row.state.value,
        "route_reason": row.route_reason,
        "pr_url": row.pr_url,
        "first_seen_at": _iso_timestamp(row.first_seen_at),
        "pr_opened_at": _iso_timestamp(row.pr_opened_at),
    }
    detail.update(
        {
            "package": row.package,
            "current_version": row.current_version,
            "fixed_version": row.fixed_version,
            "bump_kind": row.bump_kind.value if row.bump_kind is not None else None,
            "last_error": row.last_error,
            "triaged_at": _iso_timestamp(row.triaged_at),
            "updated_at": _iso_timestamp(row.updated_at),
            "session_ids": {
                "triage": triage_session.session_id if triage_session else None,
                "fix": fix_session.session_id if fix_session else None,
            },
            "route": route,
            "cves": _normalized_cves(triage_session.structured_output if triage_session else None),
            "caveats": _caveats(triage_session.structured_output if triage_session else None),
            "stage_track": _stage_track(row, triage_session, fix_session, route, now),
        }
    )
    return detail


def _normalized_cves(structured_output: object) -> list[dict]:
    try:
        if not isinstance(structured_output, dict):
            return []
        raw_cves = structured_output.get("cves")
        if not isinstance(raw_cves, list):
            return []

        cves = []
        for raw in raw_cves:
            if not isinstance(raw, dict) or not isinstance(raw.get("cve_id"), str):
                continue
            raw_verdict = raw.get("verdict")
            verdict = (
                raw_verdict
                if isinstance(raw_verdict, str) and raw_verdict in {item.value for item in Verdict}
                else "UNKNOWN"
            )
            raw_confidence = raw.get("confidence")
            confidence = (
                raw_confidence
                if isinstance(raw_confidence, str) and raw_confidence in {item.value for item in Confidence}
                else None
            )
            evidence = []
            raw_evidence = raw.get("evidence")
            if isinstance(raw_evidence, list):
                for item in raw_evidence:
                    if isinstance(item, dict):
                        file = item.get("file")
                        line = item.get("line")
                        description = item.get("description")
                        evidence.append(
                            {
                                "file": file if isinstance(file, str) else None,
                                "line": line
                                if isinstance(line, int) and not isinstance(line, bool)
                                else None,
                                "description": description if isinstance(description, str) else "",
                            }
                        )
                    elif isinstance(item, str):
                        evidence.append({"file": None, "line": None, "description": item})
            raw_gating = raw.get("gating")
            gating = raw_gating if isinstance(raw_gating, dict) else {}
            cves.append(
                {
                    "cve_id": raw["cve_id"],
                    "verdict": verdict,
                    "confidence": confidence,
                    "evidence": evidence,
                    "gating": {
                        "feature_flags": _string_list(gating.get("feature_flags")),
                        "config_options": _string_list(gating.get("config_options")),
                        "required_permissions": _string_list(gating.get("required_permissions")),
                    },
                    "reachable_by_default": (
                        raw.get("reachable_by_default")
                        if isinstance(raw.get("reachable_by_default"), bool)
                        else None
                    ),
                    "notes": raw.get("notes") if isinstance(raw.get("notes"), str) else "",
                }
            )
        return cves
    except (AttributeError, KeyError, OverflowError, TypeError, ValueError):
        return []


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _caveats(structured_output: object) -> list[str]:
    if not isinstance(structured_output, dict):
        return []
    return _string_list(structured_output.get("caveats"))


def _latest_sessions(sessions: list[SessionRow]) -> dict[int, dict[str, SessionRow]]:
    latest: dict[int, dict[str, SessionRow]] = defaultdict(dict)
    for session in sessions:
        stage_sessions = latest[session.issue_number]
        current = stage_sessions.get(session.stage.value)
        if current is None or (session.created_at, session.session_id) > (
            current.created_at,
            current.session_id,
        ):
            stage_sessions[session.stage.value] = session
    return latest


def _latest_routes(events: list[EventRow]) -> dict[int, dict]:
    latest: dict[int, EventRow] = {}
    for event in events:
        if event.issue_number is None or event.kind != "routed":
            continue
        current = latest.get(event.issue_number)
        if current is None or (event.created_at, event.id) > (current.created_at, current.id):
            latest[event.issue_number] = event

    routes = {}
    for issue_number, event in latest.items():
        action, separator, reason = event.detail.partition(": ")
        if separator and action in {item.value for item in RouteAction}:
            action_value = action
            reason_value = reason
        else:
            action_value = None
            reason_value = event.detail
        routes[issue_number] = {
            "action": action_value,
            "reason": reason_value,
            "at": _iso_timestamp(event.created_at),
        }
    return routes


def _current_issue_run(
    issue: IssueRow,
    triage_session: SessionRow | None,
    fix_session: SessionRow | None,
    route: dict,
) -> tuple[SessionRow | None, SessionRow | None, dict]:
    empty_route = {"action": None, "reason": None, "at": None}
    if issue.state in {IssueState.SEEN, IssueState.QUEUED_BUDGET}:
        return None, None, empty_route
    if issue.state is IssueState.TRIAGING:
        return triage_session, None, empty_route

    if triage_session is not None:
        triage_started_at = triage_session.created_at
        if route["at"] is not None and datetime.fromisoformat(route["at"]).timestamp() < triage_started_at:
            route = empty_route
        if fix_session is not None and fix_session.created_at < triage_started_at:
            fix_session = None
    return triage_session, fix_session, route


def _stage_track(
    issue: IssueRow,
    triage_session: SessionRow | None,
    fix_session: SessionRow | None,
    route: dict,
    now: float,
) -> list[dict]:
    state = issue.state
    route_action = route["action"]

    seen_ended = triage_session.created_at if triage_session else None
    stages = [
        {
            "key": "seen",
            "state": "done",
            "started_at": _iso_timestamp(issue.first_seen_at),
            "ended_at": _iso_timestamp(seen_ended),
            "duration_s": seen_ended - issue.first_seen_at if seen_ended is not None else None,
        }
    ]

    if triage_session is not None:
        triage_started = triage_session.created_at
        triage_ended = triage_session.settled_at
        if triage_ended is None:
            if state is IssueState.ERROR:
                triage_state = "failed"
            elif state in {IssueState.CANCELLED, IssueState.NEEDS_HUMAN}:
                triage_state = "stopped"
            else:
                triage_state = "active"
        elif (issue.triaged_at is not None and issue.triaged_at >= triage_session.created_at) or route[
            "at"
        ] is not None:
            triage_state = "done"
        elif state is IssueState.NEEDS_HUMAN:
            triage_state = "stopped"
        elif state is IssueState.ERROR:
            triage_state = "failed"
        elif state is IssueState.CANCELLED:
            triage_state = "stopped"
        else:
            triage_state = "done"
    else:
        triage_started = None
        triage_ended = None
        if state is IssueState.QUEUED_BUDGET:
            triage_state = "waiting"
        elif state is IssueState.ERROR:
            triage_state = "failed"
        elif state is IssueState.CANCELLED:
            triage_state = "stopped"
        else:
            triage_state = "pending"
    stages.append(
        {
            "key": "triage",
            "state": triage_state,
            "started_at": _iso_timestamp(triage_started),
            "ended_at": _iso_timestamp(triage_ended),
            "duration_s": _stage_duration(triage_started, triage_ended, now, triage_state),
        }
    )

    if route["at"] is not None:
        route_state = "stopped" if route_action == RouteAction.NEEDS_HUMAN else "done"
        route_started = route_ended = route["at"]
    else:
        route_state = "skipped" if triage_state in {"stopped", "failed"} else "pending"
        route_started = route_ended = None
    stages.append(
        {
            "key": "route",
            "state": route_state,
            "started_at": route_started,
            "ended_at": route_ended,
            "duration_s": None,
        }
    )

    if route_action in {RouteAction.NEEDS_HUMAN, RouteAction.CLOSE_LOW_PRIORITY} or route_state == "skipped":
        fix_state = "skipped"
        fix_started = fix_ended = None
    elif fix_session is not None:
        fix_started = fix_session.created_at
        fix_ended = fix_session.settled_at
        if fix_ended is None:
            if state is IssueState.ERROR:
                fix_state = "failed"
            elif state in {IssueState.CANCELLED, IssueState.NEEDS_HUMAN}:
                fix_state = "stopped"
            else:
                fix_state = "active"
        elif state is IssueState.PR_OPENED:
            fix_state = "done"
        elif state is IssueState.NEEDS_HUMAN:
            fix_state = "stopped"
        elif state is IssueState.ERROR:
            fix_state = "failed"
        elif state is IssueState.CANCELLED:
            fix_state = "stopped"
        else:
            fix_state = "done"
    elif route_action == RouteAction.FIX:
        fix_started = fix_ended = None
        if state is IssueState.QUEUED_BUDGET:
            fix_state = "waiting"
        elif state is IssueState.ERROR:
            fix_state = "failed"
        elif state is IssueState.CANCELLED:
            fix_state = "stopped"
        else:
            fix_state = "pending"
    else:
        fix_started = fix_ended = None
        fix_state = "pending"
    stages.append(
        {
            "key": "fix",
            "state": fix_state,
            "started_at": _iso_timestamp(fix_started),
            "ended_at": _iso_timestamp(fix_ended),
            "duration_s": _stage_duration(fix_started, fix_ended, now, fix_state),
        }
    )

    if state is IssueState.PR_OPENED:
        pr_state = "done"
        pr_started = pr_ended = issue.pr_opened_at
    elif fix_state in {"skipped", "stopped", "failed"}:
        pr_state = "skipped"
        pr_started = pr_ended = None
    else:
        pr_state = "pending"
        pr_started = pr_ended = None
    stages.append(
        {
            "key": "pr",
            "state": pr_state,
            "started_at": _iso_timestamp(pr_started),
            "ended_at": _iso_timestamp(pr_ended),
            "duration_s": pr_ended - pr_started if pr_started is not None and pr_ended is not None else None,
        }
    )

    if state is IssueState.ERROR and not any(stage["state"] == "failed" for stage in stages):
        first_incomplete = next((stage for stage in stages if stage["state"] != "done"), None)
        if first_incomplete is not None:
            first_incomplete["state"] = "failed"
    return stages


def _stage_duration(started_at: float | None, ended_at: float | None, now: float, state: str) -> float | None:
    if started_at is None:
        return None
    if state == "active":
        return now - started_at
    if ended_at is None:
        return None
    return ended_at - started_at


def _reservations(db: Database) -> list[dict]:
    with db._lock:
        rows = db._connection.execute(
            "SELECT issue_number, stage, cap, created_at, cancelled, session_id FROM ledger ORDER BY id"
        ).fetchall()
    return [
        {
            "issue_number": row["issue_number"],
            "stage": row["stage"],
            "cap": row["cap"],
            "created_at": _iso_timestamp(row["created_at"]),
            "cancelled": bool(row["cancelled"]),
            "session_id": row["session_id"],
        }
        for row in rows
    ]


def compute(db: Database, budget: Budget, settings: Settings, now: float) -> dict:
    issues = db.list_issues()
    counts = {
        "seen": len(issues),
        "in_flight": sum(issue.state in _IN_FLIGHT for issue in issues),
        "pr_opened": sum(issue.state is IssueState.PR_OPENED for issue in issues),
        "needs_human": sum(issue.state is IssueState.NEEDS_HUMAN for issue in issues),
        "not_reachable": sum(issue.state is IssueState.NOT_REACHABLE for issue in issues),
        "queued_budget": sum(issue.state is IssueState.QUEUED_BUDGET for issue in issues),
        "error": sum(issue.state is IssueState.ERROR for issue in issues),
        "cancelled": sum(issue.state is IssueState.CANCELLED for issue in issues),
    }
    denominator = counts["pr_opened"] + counts["not_reachable"] + counts["needs_human"]
    automation_rate = (
        round((counts["pr_opened"] + counts["not_reachable"]) / denominator, 3) if denominator else None
    )
    elapsed = [
        issue.pr_opened_at - issue.first_seen_at
        for issue in issues
        if issue.state is IssueState.PR_OPENED and issue.pr_opened_at is not None
    ]
    median_time = statistics.median(elapsed) if elapsed else None
    sessions = db.list_sessions()
    sessions_by_issue = _latest_sessions(sessions)
    routes_by_issue = _latest_routes(db.list_events(limit=2**63 - 1))
    reservations = _reservations(db)
    committed_by_issue: dict[int, int] = defaultdict(int)
    for reservation in reservations:
        if not reservation["cancelled"]:
            committed_by_issue[reservation["issue_number"]] += reservation["cap"]
    sync = db.latest_upstream_sync()
    branch = settings.upstream_sync_branch
    conflict_issue_number = db.get_meta(branch_meta_key("conflict_issue_number", branch))
    changelog_pr_url = db.get_meta(branch_meta_key("changelog_pr_url", branch))
    issues_detail = []
    for issue in issues:
        sessions_for_issue = sessions_by_issue.get(issue.number, {})
        triage_session, fix_session, route = _current_issue_run(
            issue,
            sessions_for_issue.get(Stage.TRIAGE.value),
            sessions_for_issue.get(Stage.FIX.value),
            routes_by_issue.get(issue.number, {"action": None, "reason": None, "at": None}),
        )
        issues_detail.append(_issue_detail(issue, triage_session, fix_session, route, now))
    return {
        "generated_at": _iso_timestamp(now),
        "mode": settings.mode.value,
        "issues": counts,
        "automation_rate": automation_rate,
        "median_time_to_pr_s": median_time,
        "acu": {
            "committed": budget.committed(),
            "ceiling": budget.ceiling,
            "remaining": budget.remaining(),
            "consumed_metered": sum(session.acus_consumed for session in sessions),
            "reservations": reservations,
            "by_issue": [
                {"issue_number": number, "committed": committed_by_issue[number]}
                for number in sorted({item["issue_number"] for item in reservations})
            ],
        },
        "upstream_sync": {
            "enabled": settings.upstream_sync_enabled,
            "last_outcome": sync["outcome"] if sync else None,
            "last_at": _iso_timestamp(sync["started_at"]) if sync else None,
            "last_detail": sync["detail"] if sync else None,
            "changelog_pr_url": changelog_pr_url,
            "conflict_issue_number": int(conflict_issue_number) if conflict_issue_number else None,
            "changelog_through_sha": db.get_meta(branch_meta_key("changelog_through_sha", branch)),
        },
        "sessions": [_session_detail(session, now) for session in sessions],
        "issues_detail": issues_detail,
    }
