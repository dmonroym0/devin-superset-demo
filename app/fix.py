"""Route triaged issues and run fix sessions. Fix sessions are never archived (PLAN §10)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.db import IssueRow, SessionRow
from app.devin_client import DevinError
from app.escalation import check_stuck, notify
from app.interfaces import Deps
from app.issue_actions import queued_budget_retry_due
from app.models import (
    FORK_REPO,
    Issue,
    IssueState,
    RouteAction,
    RouteDecision,
    SessionRequest,
    Stage,
    TriageResult,
)
from app.prompts import build_fix_prompt
from app.router import parse_issue_facts, route
from app.triage import (
    TriageParseError,
    is_settled,
    parse_triage_output,
    require_playbooks,
    session_tags,
    update_session_row,
)

if TYPE_CHECKING:
    from app.pipeline import IssueActions

NO_PR_REASON = "fix session finished without a PR"


def latest_triage_result(deps: Deps, number: int) -> TriageResult | None:
    rows = [
        row
        for row in deps.db.list_sessions(stage=Stage.TRIAGE, issue_number=number)
        if row.structured_output is not None
    ]
    if not rows:
        return None
    try:
        return parse_triage_output(rows[-1].structured_output, number)
    except TriageParseError:
        return None


async def route_triaged(deps: Deps, issue_row: IssueRow, actions: IssueActions) -> None:
    db = deps.db
    number = issue_row.number
    already_routed = issue_row.route_reason is not None
    issue = await deps.github.get_issue(number)
    facts = parse_issue_facts(issue.body)
    result = latest_triage_result(deps, number)
    decision = route(facts, result)
    fields = {
        "package": facts.package,
        "current_version": facts.current_version,
        "fixed_version": facts.fixed_version,
        "bump_kind": facts.bump_kind,
        "route_reason": decision.reason,
    }
    now = deps.clock()
    if decision.action is RouteAction.FIX and result is not None:
        if not db.transition(number, [IssueState.TRIAGED], IssueState.FIXING, now, **fields):
            return
        if not already_routed:
            db.add_event(number, "routed", f"{decision.action.value}: {decision.reason}", now)
            await notify(deps, number, "apply_route", actions.apply_route(deps, number, decision, result))
        await start_fix(deps, issue, result, decision, actions, was_queued=already_routed)
        return
    target = (
        IssueState.NOT_REACHABLE
        if decision.action is RouteAction.CLOSE_LOW_PRIORITY
        else IssueState.NEEDS_HUMAN
    )
    if db.transition(number, [IssueState.TRIAGED], target, now, **fields):
        db.add_event(number, "routed", f"{decision.action.value}: {decision.reason}", now)
        await notify(deps, number, "apply_route", actions.apply_route(deps, number, decision, result))


async def start_fix(
    deps: Deps,
    issue: Issue,
    result: TriageResult,
    decision: RouteDecision,
    actions: IssueActions,
    *,
    was_queued: bool = False,
) -> None:
    """Expects the issue already claimed (TRIAGED -> FIXING); hands it back to TRIAGED if over budget."""
    db, settings, budget = deps.db, deps.settings, deps.budget
    number = issue.number
    playbooks = require_playbooks(deps)
    cap = settings.fix_acu_cap
    reservation = budget.reserve(number, Stage.FIX, cap, deps.clock())
    if reservation is None:
        db.transition(number, [IssueState.FIXING], IssueState.TRIAGED, deps.clock())
        if not was_queued:
            db.add_event(number, "queued_budget", f"fix cap {cap} refused", deps.clock())
        if not was_queued or queued_budget_retry_due(db, number):
            await notify(
                deps,
                number,
                "mark_queued_budget",
                actions.mark_queued_budget(deps, number, budget.committed(), budget.ceiling),
            )
        return
    try:
        fresh_issue = await deps.github.get_issue(number)
        if fresh_issue.state != "open" or settings.trigger_label not in fresh_issue.labels:
            reason = "issue closed" if fresh_issue.state != "open" else "trigger label removed"
            budget.cancel(reservation)
            now = deps.clock()
            if db.transition(
                number,
                [IssueState.FIXING],
                IssueState.CANCELLED,
                now,
                route_reason=reason,
            ):
                db.add_event(number, "cancelled_before_session", reason, now)
            return
        request = SessionRequest(
            prompt=build_fix_prompt(fresh_issue, playbooks.fix, result, decision),
            title=f"fix {FORK_REPO}#{number}",
            playbook_id=playbooks.fix.playbook_id,
            max_acu_limit=cap,
            tags=session_tags(number, Stage.FIX),
            structured_output_schema=playbooks.fix.structured_output_schema,
            devin_mode=settings.devin_mode_fix,
        )
    except Exception as exc:  # noqa: BLE001
        budget.cancel(reservation)
        error = f"{type(exc).__name__}: {str(exc)[:200]}"
        db.transition(number, [IssueState.FIXING], IssueState.ERROR, deps.clock(), last_error=error)
        db.add_event(number, "fix_create_failed", error, deps.clock())
        return
    if not budget.mark_create_started(reservation, deps.clock()):
        budget.cancel(reservation)
        error = "RuntimeError: unable to mark fix session creation as started"
        db.transition(number, [IssueState.FIXING], IssueState.ERROR, deps.clock(), last_error=error)
        db.add_event(number, "fix_create_failed", error, deps.clock())
        return
    try:
        info = await deps.devin.create_session(request)
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, DevinError) and 400 <= exc.status_code < 500:
            budget.cancel(reservation)
            error = f"{type(exc).__name__}: {str(exc)[:200]}"
            db.transition(number, [IssueState.FIXING], IssueState.ERROR, deps.clock(), last_error=error)
            db.add_event(number, "fix_create_failed", error, deps.clock())
            return
        reason = "session creation outcome unknown; ACU reservation kept until reviewed"
        detail = f"{type(exc).__name__}: {str(exc)[:200]}"
        now = deps.clock()
        if db.transition(
            number,
            [IssueState.FIXING],
            IssueState.NEEDS_HUMAN,
            now,
            route_reason=reason,
        ):
            db.add_event(number, "fix_create_ambiguous", detail, now)
            await notify(deps, number, "mark_needs_human", actions.mark_needs_human(deps, number, reason))
        return
    now = deps.clock()
    budget.attach(reservation, info.session_id)
    db.insert_session(
        SessionRow(
            session_id=info.session_id,
            issue_number=number,
            stage=Stage.FIX,
            status=info.status,
            status_detail=info.status_detail,
            devin_mode=settings.devin_mode_fix or None,
            max_acu_limit=cap,
            acus_consumed=info.acus_consumed,
            url=info.url or None,
            created_at=now,
            updated_at=now,
        )
    )
    db.add_event(number, "fix_started", info.session_id, now)
    if was_queued:
        await notify(deps, number, "clear_queued_budget", actions.clear_queued_budget(deps, number))


async def check_fix(deps: Deps, session_row: SessionRow, actions: IssueActions) -> None:
    db = deps.db
    number = session_row.issue_number
    info = await deps.devin.get_session(session_row.session_id)
    update_session_row(deps, session_row, info)
    now = deps.clock()
    if info.pr_urls:
        db.update_session(session_row.session_id, settled_at=now)
        urls = info.pr_urls
        if db.transition(
            number, [IssueState.FIXING], IssueState.PR_OPENED, now, pr_url=urls[0], pr_opened_at=now
        ):
            db.add_event(number, "pr_opened", ", ".join(urls), now)
            await notify(deps, number, "mark_pr_opened", actions.mark_pr_opened(deps, number, urls))
        return
    if not is_settled(info):
        await check_stuck(deps, session_row, info, actions)
        return
    reason = (
        f"fix session suspended ({info.status_detail or 'no detail'})"
        if info.status == "suspended"
        else f"fix session errored ({info.status_detail or 'no detail'})"
        if info.status == "error"
        else NO_PR_REASON
    )
    db.update_session(session_row.session_id, settled_at=now)
    if db.transition(number, [IssueState.FIXING], IssueState.NEEDS_HUMAN, now, route_reason=reason):
        db.add_event(number, "fix_no_pr", session_row.session_id, now)
        await notify(deps, number, "mark_needs_human", actions.mark_needs_human(deps, number, reason))
