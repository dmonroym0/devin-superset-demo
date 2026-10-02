"""Read-only triage sessions: start, poll, reject any PR, parse structured output (PLAN §8)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.db import IssueRow, SessionRow
from app.escalation import archive_triage, check_stuck, notify
from app.interfaces import Deps, ResolvedPlaybooks
from app.models import (
    FORK_REPO,
    BumpKind,
    Confidence,
    CveFinding,
    IssueFacts,
    IssueState,
    SessionInfo,
    SessionRequest,
    Stage,
    TriageResult,
    Verdict,
)
from app.prompts import build_triage_prompt
from app.router import route

if TYPE_CHECKING:
    from app.pipeline import IssueActions

SETTLED_STATUSES = frozenset({"exit", "error", "suspended"})
SETTLED_DETAILS = frozenset({"finished", "waiting_for_user", "waiting_for_approval"})
TAG = "devin-superset-demo"


class TriageParseError(ValueError):
    pass


def is_settled(info: SessionInfo) -> bool:
    return info.status in SETTLED_STATUSES or info.status_detail in SETTLED_DETAILS


def session_tags(number: int, stage: Stage) -> tuple[str, ...]:
    return (TAG, f"issue-{number}", f"stage-{stage.value}")


def require_playbooks(deps: Deps) -> ResolvedPlaybooks:
    if deps.playbooks is None:
        raise RuntimeError("playbooks not resolved")
    return deps.playbooks


def _evidence(item: Any) -> str:
    if isinstance(item, dict):
        return f"{item.get('file', '?')}:{item.get('line', '?')} — {item.get('description', '')}".rstrip()
    return str(item)


def parse_triage_output(structured_output: dict[str, Any] | None, issue_number: int) -> TriageResult:
    if not isinstance(structured_output, dict):
        raise TriageParseError("missing structured output")
    if "issue_number" in structured_output:
        other = structured_output["issue_number"]
        if not isinstance(other, int) or isinstance(other, bool):
            raise TriageParseError("triage output has invalid issue_number")
        if other != issue_number:
            raise TriageParseError(f"triage output is for issue #{other}, expected #{issue_number}")
    raw_cves = structured_output.get("cves")
    if not isinstance(raw_cves, list) or not raw_cves:
        raise TriageParseError("structured output has no cves")
    cves = []
    for raw in raw_cves:
        if not isinstance(raw, dict) or not isinstance(raw.get("cve_id"), str):
            raise TriageParseError("cve entry without cve_id")
        try:
            verdict = Verdict(raw.get("verdict"))
        except ValueError:
            raise TriageParseError(f"invalid verdict for {raw['cve_id']}") from None
        try:
            confidence = Confidence(raw.get("confidence"))
        except ValueError:
            confidence = None
        evidence = raw.get("evidence") or []
        cves.append(
            CveFinding(
                cve_id=raw["cve_id"],
                verdict=verdict,
                confidence=confidence,
                evidence=tuple(_evidence(item) for item in evidence) if isinstance(evidence, list) else (),
                notes=str(raw.get("notes") or ""),
            )
        )
    caveats = structured_output.get("caveats") or []
    return TriageResult(
        issue_number=issue_number,
        cves=tuple(cves),
        package=str(structured_output.get("package") or ""),
        installed_version=str(structured_output.get("installed_version") or ""),
        head_sha=str(structured_output.get("head_sha") or ""),
        comment_url=str(structured_output.get("comment_url") or ""),
        caveats=tuple(str(caveat) for caveat in caveats) if isinstance(caveats, list) else (),
    )


async def start_triage(deps: Deps, issue_row: IssueRow, actions: IssueActions) -> None:
    db, settings, budget = deps.db, deps.settings, deps.budget
    number = issue_row.number
    was_queued = issue_row.state is IssueState.QUEUED_BUDGET
    playbooks = require_playbooks(deps)
    if not db.transition(
        number, [IssueState.SEEN, IssueState.QUEUED_BUDGET], IssueState.TRIAGING, deps.clock()
    ):
        return
    cap = settings.triage_acu_cap
    reservation = budget.reserve(number, Stage.TRIAGE, cap, deps.clock())
    if reservation is None:
        db.transition(number, [IssueState.TRIAGING], IssueState.QUEUED_BUDGET, deps.clock())
        if not was_queued:
            db.add_event(number, "queued_budget", f"triage cap {cap} refused", deps.clock())
            await notify(
                deps,
                number,
                "mark_queued_budget",
                actions.mark_queued_budget(deps, number, budget.committed(), budget.ceiling),
            )
        return
    try:
        issue = await deps.github.get_issue(number)
        request = SessionRequest(
            prompt=build_triage_prompt(issue, playbooks.triage),
            title=f"triage {FORK_REPO}#{number}",
            playbook_id=playbooks.triage.playbook_id,
            max_acu_limit=cap,
            tags=session_tags(number, Stage.TRIAGE),
            structured_output_schema=playbooks.triage.structured_output_schema,
            structured_output_required=True,
            devin_mode=settings.devin_mode_triage,
        )
        info = await deps.devin.create_session(request)
    except Exception as exc:  # noqa: BLE001
        budget.cancel(reservation)
        error = f"{type(exc).__name__}: {str(exc)[:200]}"
        db.transition(number, [IssueState.TRIAGING], IssueState.ERROR, deps.clock(), last_error=error)
        db.add_event(number, "triage_create_failed", error, deps.clock())
        return
    now = deps.clock()
    budget.attach(reservation, info.session_id)
    db.insert_session(
        SessionRow(
            session_id=info.session_id,
            issue_number=number,
            stage=Stage.TRIAGE,
            status=info.status,
            status_detail=info.status_detail,
            devin_mode=settings.devin_mode_triage or None,
            max_acu_limit=cap,
            acus_consumed=info.acus_consumed,
            url=info.url or None,
            created_at=now,
            updated_at=now,
        )
    )
    db.add_event(number, "triage_started", info.session_id, now)
    await notify(deps, number, "mark_in_progress", actions.mark_in_progress(deps, number))
    if was_queued:
        await notify(deps, number, "clear_queued_budget", actions.clear_queued_budget(deps, number))


def update_session_row(deps: Deps, row: SessionRow, info: SessionInfo) -> None:
    deps.db.update_session(
        row.session_id,
        status=info.status,
        status_detail=info.status_detail,
        acus_consumed=info.acus_consumed,
        structured_output=info.structured_output,
        url=info.url or row.url,
        updated_at=deps.clock(),
    )


_NO_FACTS = IssueFacts(None, None, None, BumpKind.UNKNOWN)


async def check_triage(deps: Deps, session_row: SessionRow, actions: IssueActions) -> None:
    db = deps.db
    number = session_row.issue_number
    info = await deps.devin.get_session(session_row.session_id)
    update_session_row(deps, session_row, info)
    now = deps.clock()

    if info.pr_urls:
        db.update_session(session_row.session_id, settled_at=now)
        decision = route(_NO_FACTS, None, rejected=True)
        db.add_event(number, "triage_rejected", ", ".join(info.pr_urls), now)
        if db.transition(
            number, [IssueState.TRIAGING], IssueState.NEEDS_HUMAN, now, route_reason=decision.reason
        ):
            await notify(
                deps,
                number,
                "apply_route",
                actions.apply_route(deps, number, decision, None, rejected_pr_urls=info.pr_urls),
            )
        await archive_triage(deps, session_row)
        return

    if info.status == "suspended":
        reason = f"triage session suspended ({info.status_detail or 'no detail'})"
        db.update_session(session_row.session_id, settled_at=now)
        if db.transition(number, [IssueState.TRIAGING], IssueState.NEEDS_HUMAN, now, route_reason=reason):
            db.add_event(number, "triage_suspended", reason, now)
            await notify(deps, number, "mark_needs_human", actions.mark_needs_human(deps, number, reason))
        await archive_triage(deps, session_row)
        return

    if not is_settled(info):
        await check_stuck(deps, session_row, info, actions)
        return

    db.update_session(session_row.session_id, settled_at=now)
    try:
        if info.status == "error":
            raise TriageParseError(f"session status {info.status}/{info.status_detail}")
        parse_triage_output(info.structured_output, number)
    except TriageParseError as exc:
        reason = f"triage output unusable: {exc}"
        if db.transition(number, [IssueState.TRIAGING], IssueState.NEEDS_HUMAN, now, route_reason=reason):
            db.add_event(number, "triage_invalid", reason, now)
            await notify(deps, number, "mark_needs_human", actions.mark_needs_human(deps, number, reason))
    else:
        if db.transition(number, [IssueState.TRIAGING], IssueState.TRIAGED, now, triaged_at=now):
            db.add_event(number, "triaged", session_row.session_id, now)
    await archive_triage(deps, session_row)
