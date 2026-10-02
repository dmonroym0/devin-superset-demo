"""Issue pipeline worker: SEEN/QUEUED_BUDGET -> TRIAGING -> TRIAGED -> FIXING -> terminal."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Sequence
from typing import Protocol

import httpx
from fastapi import FastAPI

from app.db import SessionRow
from app.devin_client import DevinError
from app.fix import check_fix, route_triaged
from app.interfaces import Deps
from app.models import TERMINAL_STATES, IssueState, RouteDecision, Stage, TriageResult
from app.playbooks import resolve_playbooks
from app.triage import check_triage, start_triage

logger = logging.getLogger(__name__)

_NON_TERMINAL = tuple(state for state in IssueState if state not in TERMINAL_STATES)


def _is_transient_poll_error(exc: Exception) -> bool:
    if isinstance(exc, DevinError):
        return exc.status_code in {0, 429} or exc.status_code >= 500
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        return status_code == 429 or status_code >= 500
    return False


class IssueActions(Protocol):
    """GitHub-side effects (child A's app.issue_actions module satisfies this structurally)."""

    def render_triage_comment(
        self, decision: RouteDecision, result: TriageResult | None, rejected_pr_urls: Sequence[str] = ()
    ) -> str: ...
    async def mark_in_progress(self, deps: Deps, number: int) -> None: ...
    async def apply_route(
        self,
        deps: Deps,
        number: int,
        decision: RouteDecision,
        result: TriageResult | None,
        *,
        rejected_pr_urls: Sequence[str] = (),
    ) -> str | None: ...
    async def mark_pr_opened(self, deps: Deps, number: int, pr_urls: Sequence[str]) -> None: ...
    async def mark_needs_human(self, deps: Deps, number: int, reason: str) -> None: ...
    async def mark_queued_budget(self, deps: Deps, number: int, committed: int, ceiling: int) -> None: ...
    async def clear_queued_budget(self, deps: Deps, number: int) -> None: ...


async def _guard(deps: Deps, number: int, step: Awaitable[None], *, transient_ok: bool = False) -> None:
    try:
        await step
    except Exception as exc:  # noqa: BLE001
        if transient_ok and _is_transient_poll_error(exc):
            status_code = getattr(exc, "status_code", None)
            if status_code is None and isinstance(exc, httpx.HTTPStatusError):
                status_code = exc.response.status_code
            detail = f"{type(exc).__name__}{f' {status_code}' if status_code is not None else ''}"
            logger.warning("transient Devin poll failed for #%s: %s", number, detail)
            deps.db.add_event(number, "devin_poll_failed", detail, deps.clock())
            return
        error = f"{type(exc).__name__}: {str(exc)[:200]}"
        logger.warning("pipeline step failed for #%s: %s", number, type(exc).__name__)
        now = deps.clock()
        deps.db.transition(number, _NON_TERMINAL, IssueState.ERROR, now, last_error=error)
        deps.db.add_event(number, "pipeline_error", error, now)


async def tick(deps: Deps, actions: IssueActions) -> None:
    db = deps.db
    for row in db.list_issues([IssueState.SEEN, IssueState.QUEUED_BUDGET]):
        await _guard(deps, row.number, start_triage(deps, row, actions))
    for session in db.list_sessions(stage=Stage.TRIAGE, active_only=True):
        issue = db.get_issue(session.issue_number)
        if issue is not None and issue.state is IssueState.TRIAGING:
            await _guard(deps, session.issue_number, check_triage(deps, session, actions), transient_ok=True)
    for row in db.list_issues([IssueState.TRIAGED]):
        await _guard(deps, row.number, route_triaged(deps, row, actions))
    for session in db.list_sessions(stage=Stage.FIX, active_only=True):
        issue = db.get_issue(session.issue_number)
        if issue is not None and issue.state is IssueState.FIXING:
            await _guard(deps, session.issue_number, check_fix(deps, session, actions), transient_ok=True)


def _recover_claims_after_restart(deps: Deps) -> None:
    recoveries = (
        (IssueState.TRIAGING, Stage.TRIAGE, IssueState.SEEN),
        (IssueState.FIXING, Stage.FIX, IssueState.TRIAGED),
    )
    for claimed_state, stage, target_state in recoveries:
        for issue in deps.db.list_issues([claimed_state]):
            active_sessions = deps.db.list_sessions(stage=stage, issue_number=issue.number, active_only=True)
            if active_sessions:
                continue
            readopted = deps.budget.attached_without_session(issue.number, stage)
            if readopted:
                devin_mode = (
                    deps.settings.devin_mode_triage if stage is Stage.TRIAGE else deps.settings.devin_mode_fix
                )
                for _, session_id, cap, reserved_at in readopted:
                    deps.db.insert_session(
                        SessionRow(
                            session_id=session_id,
                            issue_number=issue.number,
                            stage=stage,
                            status="running",
                            status_detail=None,
                            devin_mode=devin_mode,
                            max_acu_limit=cap,
                            acus_consumed=0.0,
                            url=None,
                            created_at=reserved_at,
                            updated_at=reserved_at,
                        )
                    )
                    deps.db.add_event(issue.number, "readopted_after_restart", session_id, deps.clock())
                    logger.info("readopted session %s for issue #%s after restart", session_id, issue.number)
                continue
            cancelled = deps.budget.cancel_unattached(issue.number, stage)
            now = deps.clock()
            if deps.db.transition(issue.number, [claimed_state], target_state, now):
                detail = (
                    f"{claimed_state.value} -> {target_state.value}; cancelled {cancelled} "
                    f"unattached {stage.value} reservation(s)"
                )
                deps.db.add_event(issue.number, "recovered_after_restart", detail, now)
                logger.info("recovered issue #%s after restart: %s", issue.number, detail)


def register(app: FastAPI, deps: Deps, actions: IssueActions | None = None) -> None:
    if actions is None:
        from app import issue_actions

        actions = issue_actions
    resolved_actions = actions
    app.state.issue_actions = resolved_actions

    async def resolve() -> None:
        deps.playbooks = await resolve_playbooks(deps.settings, deps.devin)
        logger.info(
            "Playbooks resolved: triage=%s fix=%s", deps.playbooks.triage.title, deps.playbooks.fix.title
        )
        _recover_claims_after_restart(deps)

    async def worker() -> None:
        while True:
            if deps.playbooks is not None:
                try:
                    await tick(deps, resolved_actions)
                except Exception:
                    logger.exception("pipeline tick failed")
            try:
                await asyncio.wait_for(deps.wake.wait(), timeout=deps.settings.poll_interval_s)
            except TimeoutError:
                pass
            deps.wake.clear()

    deps.startup.append(resolve)
    deps.background.append(worker)
