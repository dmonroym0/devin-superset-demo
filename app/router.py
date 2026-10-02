"""Per-issue routing (docs/PLAN.md §9). Pure functions."""

from __future__ import annotations

import re

from app.models import (
    BumpKind,
    Confidence,
    CveFinding,
    IssueFacts,
    RouteAction,
    RouteDecision,
    TriageResult,
    Verdict,
)

_PACKAGE = re.compile(r"^\s*Package:\s*(\S+)", re.MULTILINE)
_VERSIONS = re.compile(r"^\s*Current\s*(?:->|→)\s*fixed:\s*(\S+)\s*(?:->|→)\s*(\S+)", re.MULTILINE)
_CVES = re.compile(r"^\s*CVEs:\s*(.*)$", re.MULTILINE)
_CVE_ID = re.compile(r"CVE-\d{4}-\d+", re.IGNORECASE)
_VERSION = re.compile(r"v?(\d+)(?:\.(\d+))?")
_QUALIFYING_CONFIDENCE = frozenset({Confidence.HIGH, Confidence.MEDIUM})


def _numeric(version: str) -> tuple[int, int] | None:
    match = _VERSION.match(version)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2) or 0)


def bump_kind(current: str | None, fixed: str | None) -> BumpKind:
    if not current or not fixed:
        return BumpKind.UNKNOWN
    old, new = _numeric(current), _numeric(fixed)
    if old is None or new is None:
        return BumpKind.UNKNOWN
    if new[0] > old[0]:
        return BumpKind.MAJOR
    if new[0] < old[0]:
        return BumpKind.UNKNOWN
    if new[1] != old[1]:
        return BumpKind.MINOR
    return BumpKind.PATCH


def parse_issue_facts(body: str) -> IssueFacts:
    package = _PACKAGE.search(body or "")
    versions = _VERSIONS.search(body or "")
    cves = _CVES.search(body or "")
    current = versions.group(1) if versions else None
    fixed = versions.group(2) if versions else None
    cve_ids = tuple(dict.fromkeys(cve.upper() for cve in _CVE_ID.findall(cves.group(1)))) if cves else ()
    return IssueFacts(
        package=package.group(1) if package else None,
        current_version=current,
        fixed_version=fixed,
        bump_kind=bump_kind(current, fixed),
        cve_ids=cve_ids,
    )


def _qualifies(cve: CveFinding) -> bool:
    return cve.verdict is Verdict.REACHABLE and cve.effective_confidence in _QUALIFYING_CONFIDENCE


def route(facts: IssueFacts, result: TriageResult | None, *, rejected: bool = False) -> RouteDecision:
    if rejected:
        return RouteDecision(
            RouteAction.NEEDS_HUMAN, "triage session opened a pull request (read-only violated)"
        )
    if result is None:
        return RouteDecision(RouteAction.NEEDS_HUMAN, "no valid triage result")

    if not facts.cve_ids:
        return RouteDecision(
            RouteAction.NEEDS_HUMAN,
            "issue lists no CVE IDs; cannot validate triage output",
        )

    listed = {cve_id.upper() for cve_id in facts.cve_ids}
    findings_from_issue = tuple(cve for cve in result.cves if cve.cve_id.upper() in listed)
    unexpected = tuple(cve.cve_id for cve in result.cves if cve.cve_id.upper() not in listed)
    returned = {cve.cve_id.upper() for cve in findings_from_issue}
    missing = tuple(
        CveFinding(cve_id, Verdict.UNKNOWN, Confidence.LOW, notes="no verdict returned")
        for cve_id in facts.cve_ids
        if cve_id.upper() not in returned
    )
    findings = findings_from_issue + missing
    unexpected_ids = tuple(unexpected)

    if facts.bump_kind is BumpKind.MAJOR:
        return RouteDecision(
            RouteAction.NEEDS_HUMAN,
            f"major version bump {facts.current_version} -> {facts.fixed_version}",
            others=findings,
            unexpected=unexpected_ids,
        )
    qualifying = tuple(cve for cve in findings if _qualifies(cve))
    if qualifying:
        if facts.bump_kind is BumpKind.UNKNOWN:
            return RouteDecision(
                RouteAction.NEEDS_HUMAN,
                "could not confirm the bump is not major",
                others=findings,
                unexpected=unexpected_ids,
            )
        others = tuple(cve for cve in findings if not _qualifies(cve))
        ids = ", ".join(cve.cve_id for cve in qualifying)
        return RouteDecision(
            RouteAction.FIX,
            f"reachable with medium or high confidence: {ids}",
            qualifying,
            others,
            unexpected_ids,
        )
    if findings and all(cve.verdict is Verdict.NOT_REACHABLE for cve in findings):
        return RouteDecision(
            RouteAction.CLOSE_LOW_PRIORITY,
            "all CVEs not reachable",
            others=findings,
            unexpected=unexpected_ids,
        )
    return RouteDecision(
        RouteAction.NEEDS_HUMAN,
        "no CVE is reachable with medium or high confidence",
        others=findings,
        unexpected=unexpected_ids,
    )
