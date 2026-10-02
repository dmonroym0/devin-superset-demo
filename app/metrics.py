"""Metrics snapshots for the dashboard and API."""

from __future__ import annotations

import statistics
from datetime import UTC, datetime

from app.budget import Budget
from app.config import Settings
from app.db import Database, IssueRow, SessionRow
from app.models import IssueState
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


def _session_detail(row: SessionRow) -> dict:
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
    }


def _issue_detail(row: IssueRow) -> dict:
    return {
        "number": row.number,
        "title": row.title,
        "state": row.state.value,
        "route_reason": row.route_reason,
        "pr_url": row.pr_url,
        "first_seen_at": _iso_timestamp(row.first_seen_at),
        "pr_opened_at": _iso_timestamp(row.pr_opened_at),
    }


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
    sync = db.latest_upstream_sync()
    branch = settings.upstream_sync_branch
    conflict_issue_number = db.get_meta(branch_meta_key("conflict_issue_number", branch))
    changelog_pr_url = db.get_meta(branch_meta_key("changelog_pr_url", branch))
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
        },
        "upstream_sync": {
            "enabled": settings.upstream_sync_enabled,
            "last_outcome": sync["outcome"] if sync else None,
            "last_at": _iso_timestamp(sync["started_at"]) if sync else None,
            "changelog_pr_url": changelog_pr_url,
            "conflict_issue_number": int(conflict_issue_number) if conflict_issue_number else None,
            "changelog_through_sha": db.get_meta(branch_meta_key("changelog_through_sha", branch)),
        },
        "sessions": [_session_detail(session) for session in sessions],
        "issues_detail": [_issue_detail(issue) for issue in issues],
    }
