"""GitHub issue comments, labels, and state changes."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from app.github_client import GitHubError
from app.interfaces import Deps
from app.models import (
    LABEL_IN_PROGRESS,
    LABEL_LOW_PRIORITY,
    LABEL_NEEDS_HUMAN,
    LABEL_PR_OPENED,
    LABEL_QUEUED_BUDGET,
    LABEL_TRIAGE_REJECTED,
    RouteAction,
    RouteDecision,
    TriageResult,
)

logger = logging.getLogger(__name__)


def _escape(text: str) -> str:
    return (
        text.replace("\r\n", " ")
        .replace("\n", " ")
        .replace("\r", " ")
        .replace("|", "\\|")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def render_triage_comment(
    decision: RouteDecision,
    result: TriageResult | None,
    rejected_pr_urls: Sequence[str] = (),
) -> str:
    if decision.action is RouteAction.FIX:
        route = "**Route:** Fix: opening a Devin fix session"
    elif decision.action is RouteAction.NEEDS_HUMAN:
        route = f"**Route:** Needs a human: {_escape(decision.reason)}"
    else:
        route = "**Route:** Not reachable: closing as low priority"

    findings = (*decision.qualifying, *decision.others)
    lines = ["### devin-superset-demo triage", "", route, ""]
    if findings:
        lines.extend(
            [
                "| CVE | Verdict | Confidence | Evidence |",
                "| --- | --- | --- | --- |",
            ]
        )
        for finding in findings:
            evidence = finding.evidence[0] if finding.evidence else "—"
            lines.append(
                f"| {_escape(finding.cve_id)} | {_escape(finding.verdict.value)} | "
                f"{_escape(finding.effective_confidence.value)} | {_escape(evidence)} |"
            )
    else:
        lines.append("_No CVE findings._")
    if rejected_pr_urls:
        lines.extend(
            [
                "",
                "**Triage result rejected: the read-only triage session opened PR(s):**",
                *(f"- <{_escape(url)}>" for url in rejected_pr_urls),
            ]
        )
    if result and result.comment_url:
        lines.extend(["", f"[full triage comment]({_escape(result.comment_url)})"])
    lines.extend(["", "_Automated by devin-superset-demo._"])
    return "\n".join(lines)


async def _call(deps: Deps, number: int, call):
    try:
        return await call()
    except GitHubError as err:
        logger.warning("%s", str(err))
        deps.db.add_event(
            number,
            "github_error",
            f"{err.method} {err.path} {err.status_code}",
            deps.clock(),
        )
        return None


async def _add_labels(deps: Deps, number: int, labels: Sequence[str]) -> None:
    await _call(deps, number, lambda: deps.github.add_labels(number, list(labels)))


async def _remove_label(deps: Deps, number: int, label: str) -> None:
    await _call(deps, number, lambda: deps.github.remove_label(number, label))


async def _comment(deps: Deps, number: int, body: str) -> str | None:
    return await _call(deps, number, lambda: deps.github.create_comment(number, body))


async def _close(deps: Deps, number: int, reason: str) -> None:
    await _call(deps, number, lambda: deps.github.close_issue(number, reason))


async def mark_in_progress(deps: Deps, number: int) -> None:
    await _add_labels(deps, number, [LABEL_IN_PROGRESS])


async def apply_route(
    deps: Deps,
    number: int,
    decision: RouteDecision,
    result: TriageResult | None,
    *,
    rejected_pr_urls: Sequence[str] = (),
) -> str | None:
    comment_url = await _comment(deps, number, render_triage_comment(decision, result, rejected_pr_urls))
    if decision.action is RouteAction.FIX:
        return comment_url
    if decision.action is RouteAction.NEEDS_HUMAN:
        labels = [LABEL_NEEDS_HUMAN]
        if rejected_pr_urls:
            labels.append(LABEL_TRIAGE_REJECTED)
        await _add_labels(deps, number, labels)
        await _remove_label(deps, number, LABEL_IN_PROGRESS)
        return comment_url
    await _add_labels(deps, number, [LABEL_LOW_PRIORITY])
    await _remove_label(deps, number, LABEL_IN_PROGRESS)
    await _close(deps, number, "not_planned")
    return comment_url


async def mark_pr_opened(deps: Deps, number: int, pr_urls: Sequence[str]) -> None:
    urls = "\n".join(f"- <{_escape(url)}>" for url in pr_urls)
    body = f"{urls}\n\nOpened, not done: CI and review continue in the Devin session."
    await _comment(deps, number, body)
    await _add_labels(deps, number, [LABEL_PR_OPENED])
    await _remove_label(deps, number, LABEL_IN_PROGRESS)


async def mark_needs_human(deps: Deps, number: int, reason: str) -> None:
    await _comment(deps, number, f"Needs a human: {_escape(reason)}")
    await _add_labels(deps, number, [LABEL_NEEDS_HUMAN])
    await _remove_label(deps, number, LABEL_IN_PROGRESS)


async def mark_queued_budget(deps: Deps, number: int, committed: int, ceiling: int) -> None:
    issue = await _call(deps, number, lambda: deps.github.get_issue(number))
    if issue is not None and LABEL_QUEUED_BUDGET in issue.labels:
        return
    await _add_labels(deps, number, [LABEL_QUEUED_BUDGET])
    await _comment(
        deps,
        number,
        f"Queued for the next retry: committed {committed} / ceiling {ceiling} ACUs.",
    )


async def clear_queued_budget(deps: Deps, number: int) -> None:
    await _remove_label(deps, number, LABEL_QUEUED_BUDGET)
