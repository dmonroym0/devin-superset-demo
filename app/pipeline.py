"""Issue pipeline worker: SEEN/QUEUED_BUDGET -> TRIAGING -> TRIAGED -> FIXING -> terminal."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Sequence
from typing import Protocol

from fastapi import FastAPI

from app.fix import check_fix, route_triaged
from app.interfaces import Deps
from app.models import TERMINAL_STATES, IssueState, RouteDecision, Stage, TriageResult
from app.playbooks import resolve_playbooks
from app.triage import check_triage, start_triage

logger = logging.getLogger(__name__)

_NON_TERMINAL = tuple(state for state in IssueState if state not in TERMINAL_STATES)


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


async def _guard(deps: Deps, number: int, step: Awaitable[None]) -> None:
    try:
        await step
    except Exception as exc:  # noqa: BLE001
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
            await _guard(deps, session.issue_number, check_triage(deps, session, actions))
    for row in db.list_issues([IssueState.TRIAGED]):
        await _guard(deps, row.number, route_triaged(deps, row, actions))
    for session in db.list_sessions(stage=Stage.FIX, active_only=True):
        issue = db.get_issue(session.issue_number)
        if issue is not None and issue.state is IssueState.FIXING:
            await _guard(deps, session.issue_number, check_fix(deps, session, actions))


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
