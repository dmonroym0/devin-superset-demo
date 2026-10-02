"""Stuck-session escalation: one soft nudge, then needs-human on hard timeout or suspension (PLAN §11)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from typing import TYPE_CHECKING

from app.db import SessionRow
from app.interfaces import Deps
from app.models import IssueState, SessionInfo, Stage

if TYPE_CHECKING:
    from app.pipeline import IssueActions

logger = logging.getLogger(__name__)

NUDGE_MESSAGE = (
    "Status check from devin-superset-demo: please finish and report structured output, "
    "or say what blocks you."
)
LIMIT_DETAILS = frozenset(
    {
        "usage_limit_exceeded",
        "out_of_quota",
        "out_of_credits",
        "no_quota_allocation",
        "payment_declined",
        "org_usage_limit_exceeded",
        "user_usage_limit_exceeded",
        "total_session_limit_exceeded",
        "contract_expired",
        "error",
    }
)


async def notify(deps: Deps, number: int, name: str, call: Awaitable[object]) -> None:
    """Run a GitHub-side action; failures are logged and recorded but never block the state machine."""
    try:
        await call
    except Exception as exc:  # noqa: BLE001
        logger.warning("issue action %s failed for #%s: %s", name, number, type(exc).__name__)
        deps.db.add_event(number, "action_failed", f"{name}: {type(exc).__name__}", deps.clock())


async def archive_triage(deps: Deps, row: SessionRow) -> None:
    if row.stage is not Stage.TRIAGE or row.archived:
        return
    try:
        await deps.devin.archive_session(row.session_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("archive of %s failed: %s", row.session_id, type(exc).__name__)
        deps.db.add_event(row.issue_number, "archive_failed", type(exc).__name__, deps.clock())
        return
    deps.db.update_session(row.session_id, archived=True)


async def check_stuck(deps: Deps, session_row: SessionRow, info: SessionInfo, actions: IssueActions) -> bool:
    now = deps.clock()
    settings = deps.settings
    number = session_row.issue_number
    age = now - session_row.created_at
    reason: str | None = None
    if info.status == "error":
        reason = f"{session_row.stage.value} session errored"
    elif info.status_detail in LIMIT_DETAILS:
        reason = f"{session_row.stage.value} session stopped: {info.status_detail}"
    elif age >= settings.hard_timeout_s:
        reason = f"{session_row.stage.value} session did not finish within {settings.hard_timeout_s}s"
    if reason:
        deps.db.update_session(session_row.session_id, settled_at=now, updated_at=now)
        active = IssueState.TRIAGING if session_row.stage is Stage.TRIAGE else IssueState.FIXING
        if deps.db.transition(number, [active], IssueState.NEEDS_HUMAN, now, route_reason=reason):
            deps.db.add_event(number, "escalated", reason, now)
            await notify(deps, number, "mark_needs_human", actions.mark_needs_human(deps, number, reason))
        await archive_triage(deps, session_row)
        return True
    if age >= settings.soft_timeout_s and not session_row.nudged:
        try:
            await deps.devin.send_message(session_row.session_id, NUDGE_MESSAGE)
        except Exception as exc:  # noqa: BLE001
            deps.db.add_event(number, "nudge_failed", type(exc).__name__, now)
            return False
        deps.db.update_session(session_row.session_id, nudged=True)
        deps.db.add_event(number, "nudged", session_row.session_id, now)
    return False
