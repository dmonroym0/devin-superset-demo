"""GitHub issue comments, labels, and state changes."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence

from app.db import Database
from app.github_client import GitHubError
from app.interfaces import Deps
from app.models import (
    LABEL_IN_PROGRESS,
    LABEL_LOW_PRIORITY,
    LABEL_NEEDS_HUMAN,
    LABEL_PR_OPENED,
    LABEL_QUEUED_BUDGET,
    LABEL_TRIAGE_REJECTED,
    FORK_REPO,
    RouteAction,
    RouteDecision,
    TriageResult,
)

logger = logging.getLogger(__name__)

_FORK_PR_URL = re.compile(rf"^https://github\.com/{re.escape(FORK_REPO)}/pull/\d+$")
_MARKDOWN_SPECIAL = frozenset(r"\`*_{}[]()#+-!|~")


def _escape(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"[\r\n]+", " ", text)
    return "".join(f"\\{char}" if char in _MARKDOWN_SPECIAL else char for char in text)


def is_fork_pr_url(url: str | None) -> bool:
    return isinstance(url, str) and _FORK_PR_URL.fullmatch(url) is not None


def _safe_comment_url(url: str, issue_number: int) -> bool:
    pattern = re.compile(
        rf"^https://github\.com/{re.escape(FORK_REPO)}/issues/{issue_number}#issuecomment-\d+$"
    )
    return pattern.fullmatch(url) is not None


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
    if decision.unexpected:
        lines.append(
            f"**Ignored (not listed in the issue):** {_escape(', '.join(decision.unexpected))}"
        )
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
                *(
                    f"- <{url}>" if is_fork_pr_url(url) else "- (unsafe URL omitted)"
                    for url in rejected_pr_urls
                ),
            ]
        )
    if result and result.comment_url and _safe_comment_url(result.comment_url, result.issue_number):
        lines.extend(["", f"[full triage comment]({result.comment_url})"])
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


async def _remove_label(deps: Deps, number: int, label: str) -> bool:
    async def remove() -> bool:
        await deps.github.remove_label(number, label)
        return True

    return await _call(deps, number, remove) is True


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
    urls = "\n".join(
        f"- <{url}>" if is_fork_pr_url(url) else "- (unsafe URL omitted)" for url in pr_urls
    )
    body = f"{urls}\n\nOpened, not done: CI and review continue in the Devin session."
    await _comment(deps, number, body)
    await _add_labels(deps, number, [LABEL_PR_OPENED])
    await _remove_label(deps, number, LABEL_IN_PROGRESS)


async def mark_needs_human(deps: Deps, number: int, reason: str) -> None:
    await _comment(deps, number, f"Needs a human: {_escape(reason)}")
    await _add_labels(deps, number, [LABEL_NEEDS_HUMAN])
    await _remove_label(deps, number, LABEL_IN_PROGRESS)


async def mark_queued_budget(deps: Deps, number: int, committed: int, ceiling: int) -> None:
    await _add_labels(deps, number, [LABEL_QUEUED_BUDGET])
    for event in deps.db.list_events(issue_number=number):
        if event.kind == "queued_budget_cleared":
            break
        if event.kind == "queued_budget_commented":
            return
    comment_url = await _comment(
        deps,
        number,
        f"Queued for the next retry: committed {committed} / ceiling {ceiling} ACUs.",
    )
    if comment_url:
        deps.db.add_event(
            number,
            "queued_budget_commented",
            f"committed {committed} / ceiling {ceiling}",
            deps.clock(),
        )
    else:
        deps.db.add_event(
            number,
            "queued_budget_comment_failed",
            f"committed {committed} / ceiling {ceiling}",
            deps.clock(),
        )


async def clear_queued_budget(deps: Deps, number: int) -> None:
    if await _remove_label(deps, number, LABEL_QUEUED_BUDGET):
        deps.db.add_event(number, "queued_budget_cleared", "label removed", deps.clock())


def queued_budget_retry_due(db: Database, number: int) -> bool:
    """True when the latest queued-budget comment attempt in this queue cycle failed."""
    for event in db.list_events(issue_number=number):
        if event.kind == "queued_budget_comment_failed":
            return True
        if event.kind in {"queued_budget_commented", "queued_budget_cleared"}:
            return False
    return False
