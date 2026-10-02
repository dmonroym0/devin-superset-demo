import json
from pathlib import Path

import pytest

from app.models import BumpKind, Confidence, CveFinding, IssueFacts, RouteAction, TriageResult, Verdict
from app.router import bump_kind, parse_issue_facts, route
from app.triage import parse_triage_output

DEMO = Path(__file__).resolve().parent.parent / "app" / "demo"
PATCH = IssueFacts("pkg", "1.0.0", "1.0.1", BumpKind.PATCH, ("CVE-1",))


def cve(cve_id, verdict, confidence=None):
    return CveFinding(cve_id, verdict, confidence)


def result(*cves):
    return TriageResult(1, tuple(cves))


def test_parse_issue_facts_ignores_untrusted_bump_annotation():
    body = "Package: foo (pip)\nCurrent -> fixed: 7.1.0 -> 9.0.2 (minor)\nCVEs: CVE-2026-1, CVE-2026-2\n"
    facts = parse_issue_facts(body)
    assert facts == IssueFacts("foo", "7.1.0", "9.0.2", BumpKind.MAJOR, ("CVE-2026-1", "CVE-2026-2"))


def test_parse_issue_facts_accepts_unicode_arrow():
    facts = parse_issue_facts("Package: bar\nCurrent → fixed: 1.2.3 → 1.3.0\nCVEs: CVE-2026-9")
    assert (facts.current_version, facts.fixed_version, facts.bump_kind) == ("1.2.3", "1.3.0", BumpKind.MINOR)


@pytest.mark.parametrize(
    ("current", "fixed", "kind"),
    [
        ("1.2.3", "1.2.4", BumpKind.PATCH),
        ("1.2.3", "1.3.0", BumpKind.MINOR),
        ("1.2.3", "2.0.0", BumpKind.MAJOR),
        ("v1.2", "v1.2.9", BumpKind.PATCH),
        (None, "1.0", BumpKind.UNKNOWN),
        ("abc", "1.0", BumpKind.UNKNOWN),
    ],
)
def test_bump_kind(current, fixed, kind):
    assert bump_kind(current, fixed) is kind


def test_missing_lines_are_unknown():
    facts = parse_issue_facts("nothing useful here")
    assert facts.bump_kind is BumpKind.UNKNOWN and facts.cve_ids == () and facts.package is None


def test_reachable_medium_plus_unknown_is_fix_with_unknown_in_others():
    facts = IssueFacts("pkg", "1.0.0", "1.0.1", BumpKind.PATCH, ("CVE-1", "CVE-2"))
    decision = route(
        facts, result(cve("CVE-1", Verdict.REACHABLE, Confidence.MEDIUM), cve("CVE-2", Verdict.UNKNOWN))
    )
    assert decision.action is RouteAction.FIX
    assert [c.cve_id for c in decision.qualifying] == ["CVE-1"]
    assert [c.cve_id for c in decision.others] == ["CVE-2"]


def test_all_unknown_is_needs_human():
    assert route(PATCH, result(cve("CVE-1", Verdict.UNKNOWN))).action is RouteAction.NEEDS_HUMAN


def test_major_bump_with_reachable_high_is_needs_human():
    facts = IssueFacts("pkg", "7.0", "9.0", BumpKind.MAJOR, ("CVE-1",))
    decision = route(facts, result(cve("CVE-1", Verdict.REACHABLE, Confidence.HIGH)))
    assert decision.action is RouteAction.NEEDS_HUMAN
    assert decision.reason == "major version bump 7.0 -> 9.0"


def test_reachable_low_only_is_needs_human():
    decision = route(PATCH, result(cve("CVE-1", Verdict.REACHABLE, Confidence.LOW)))
    assert decision.action is RouteAction.NEEDS_HUMAN


def test_missing_confidence_counts_as_low():
    assert route(PATCH, result(cve("CVE-1", Verdict.REACHABLE))).action is RouteAction.NEEDS_HUMAN


def test_all_not_reachable_is_close():
    decision = route(PATCH, result(cve("CVE-1", Verdict.NOT_REACHABLE, Confidence.HIGH)))
    assert decision.action is RouteAction.CLOSE_LOW_PRIORITY


def test_not_reachable_plus_unknown_is_needs_human():
    facts = IssueFacts("pkg", "1.0.0", "1.0.1", BumpKind.PATCH, ("CVE-1", "CVE-2"))
    decision = route(
        facts,
        result(cve("CVE-1", Verdict.NOT_REACHABLE, Confidence.HIGH), cve("CVE-2", Verdict.UNKNOWN)),
    )
    assert decision.action is RouteAction.NEEDS_HUMAN


def test_unknown_bump_with_reachable_is_needs_human():
    facts = IssueFacts("pkg", None, None, BumpKind.UNKNOWN, ("CVE-1",))
    decision = route(facts, result(cve("CVE-1", Verdict.REACHABLE, Confidence.HIGH)))
    assert decision.action is RouteAction.NEEDS_HUMAN
    assert decision.reason == "could not confirm the bump is not major"


def test_empty_result_and_none_and_rejected_are_needs_human():
    empty = IssueFacts("pkg", "1.0", "1.0.1", BumpKind.PATCH)
    assert route(empty, result()).action is RouteAction.NEEDS_HUMAN
    assert route(PATCH, None).action is RouteAction.NEEDS_HUMAN
    reachable = result(cve("CVE-1", Verdict.REACHABLE, Confidence.HIGH))
    assert route(PATCH, reachable, rejected=True).action is RouteAction.NEEDS_HUMAN


def test_cve_missing_from_result_is_added_as_unknown_low():
    facts = IssueFacts("pkg", "1.0", "1.0.1", BumpKind.PATCH, ("CVE-1", "CVE-2"))
    decision = route(facts, result(cve("CVE-1", Verdict.NOT_REACHABLE, Confidence.HIGH)))
    assert decision.action is RouteAction.NEEDS_HUMAN
    missing = [c for c in decision.others if c.cve_id == "CVE-2"]
    assert missing and missing[0].verdict is Verdict.UNKNOWN and missing[0].confidence is Confidence.LOW
    assert missing[0].notes == "no verdict returned"


def test_unlisted_reachable_cve_does_not_route_to_fix():
    facts = IssueFacts(
        "pkg", "6.0.1", "6.1.0", BumpKind.MINOR, ("CVE-2026-1111",)
    )
    decision = route(
        facts,
        result(cve("CVE-2026-9999", Verdict.REACHABLE, Confidence.HIGH)),
    )

    assert decision.action is RouteAction.NEEDS_HUMAN
    assert [(item.cve_id, item.verdict) for item in decision.others] == [
        ("CVE-2026-1111", Verdict.UNKNOWN)
    ]
    assert decision.unexpected == ("CVE-2026-9999",)


def test_only_issue_listed_cves_can_qualify():
    facts = IssueFacts(
        "pkg", "6.0.1", "6.1.0", BumpKind.MINOR, ("CVE-2026-1111",)
    )
    decision = route(
        facts,
        result(
            cve("CVE-2026-9999", Verdict.REACHABLE, Confidence.HIGH),
            cve("CVE-2026-1111", Verdict.REACHABLE, Confidence.MEDIUM),
        ),
    )

    assert decision.action is RouteAction.FIX
    assert [item.cve_id for item in decision.qualifying] == ["CVE-2026-1111"]
    assert decision.unexpected == ("CVE-2026-9999",)


def test_issue_without_cve_ids_requires_human_review():
    facts = IssueFacts("pkg", "6.0.1", "6.1.0", BumpKind.MINOR)
    decision = route(
        facts,
        result(cve("CVE-2026-1111", Verdict.REACHABLE, Confidence.HIGH)),
    )

    assert decision.action is RouteAction.NEEDS_HUMAN
    assert decision.reason == "issue lists no CVE IDs; cannot validate triage output"


@pytest.mark.parametrize(
    ("number", "action"),
    [
        (1, RouteAction.FIX),
        (2, RouteAction.CLOSE_LOW_PRIORITY),
        (3, RouteAction.FIX),
        (4, RouteAction.NEEDS_HUMAN),
        (5, RouteAction.NEEDS_HUMAN),
    ],
)
def test_seeded_scenarios(number, action):
    seeds = {item["number"]: item for item in json.loads((DEMO / "seed_issues.json").read_text())["issues"]}
    scenarios = json.loads((DEMO / "scenarios.json").read_text())["issues"]
    facts = parse_issue_facts(seeds[number]["body"])
    triage = parse_triage_output(scenarios[str(number)]["triage"]["structured_output"], number)
    decision = route(facts, triage)
    assert decision.action is action
    if number == 4:
        assert decision.reason.startswith("major version bump 7")
