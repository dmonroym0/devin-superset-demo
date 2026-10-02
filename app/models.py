"""Shared data contracts. Every module builds against these types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

FORK_REPO = "dmonroym0/superset"
UPSTREAM_REPO = "apache/superset"


class Mode(StrEnum):
    DEMO = "demo"
    LIVE = "live"


class IssueState(StrEnum):
    SEEN = "seen"
    QUEUED_BUDGET = "queued_budget"
    TRIAGING = "triaging"
    TRIAGED = "triaged"
    FIXING = "fixing"
    PR_OPENED = "pr_opened"
    NEEDS_HUMAN = "needs_human"
    NOT_REACHABLE = "not_reachable"
    ERROR = "error"
    CANCELLED = "cancelled"


TERMINAL_STATES: frozenset[IssueState] = frozenset(
    {
        IssueState.PR_OPENED,
        IssueState.NEEDS_HUMAN,
        IssueState.NOT_REACHABLE,
        IssueState.ERROR,
        IssueState.CANCELLED,
    }
)


class Stage(StrEnum):
    TRIAGE = "triage"
    FIX = "fix"


class Verdict(StrEnum):
    REACHABLE = "REACHABLE"
    NOT_REACHABLE = "NOT_REACHABLE"
    UNKNOWN = "UNKNOWN"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class BumpKind(StrEnum):
    MAJOR = "major"
    MINOR = "minor"
    PATCH = "patch"
    UNKNOWN = "unknown"


class RouteAction(StrEnum):
    FIX = "fix"
    NEEDS_HUMAN = "needs_human"
    CLOSE_LOW_PRIORITY = "close_low_priority"


@dataclass(frozen=True)
class LabelSpec:
    name: str
    color: str
    description: str


LABEL_TRIGGER = "devin:fixplease"
LABEL_IN_PROGRESS = "devin:in-progress"
LABEL_PR_OPENED = "devin:pr-opened"
LABEL_NEEDS_HUMAN = "devin:needs-human"
LABEL_LOW_PRIORITY = "devin:low-priority"
LABEL_QUEUED_BUDGET = "devin:queued-budget"
LABEL_TRIAGE_REJECTED = "devin:triage-rejected"

MANAGED_LABELS: tuple[LabelSpec, ...] = (
    LabelSpec(LABEL_TRIGGER, "5319e7", "Ask Devin to triage and fix this security issue"),
    LabelSpec(LABEL_IN_PROGRESS, "1d76db", "Devin is triaging or fixing this issue"),
    LabelSpec(LABEL_PR_OPENED, "0e8a16", "Devin opened a PR for this issue"),
    LabelSpec(LABEL_NEEDS_HUMAN, "d93f0b", "Needs a human decision"),
    LabelSpec(LABEL_LOW_PRIORITY, "c5def5", "Not reachable; low priority"),
    LabelSpec(LABEL_QUEUED_BUDGET, "fbca04", "Waiting for ACU budget"),
    LabelSpec(LABEL_TRIAGE_REJECTED, "b60205", "Triage result rejected (read-only violated)"),
)


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    body: str
    labels: tuple[str, ...] = ()
    state: str = "open"
    html_url: str = ""
    repo: str = FORK_REPO
    is_pull_request: bool = False


@dataclass(frozen=True)
class MergeUpstreamResult:
    outcome: Literal["merged", "fast-forward", "none", "conflict", "error"]
    message: str


@dataclass(frozen=True)
class UpstreamCommit:
    sha: str
    subject: str
    is_merge: bool


@dataclass(frozen=True)
class CompareResult:
    commits: tuple[UpstreamCommit, ...]
    files: tuple[str, ...]


@dataclass(frozen=True)
class PullRequestRef:
    pr_url: str
    pr_state: str | None = None


@dataclass(frozen=True)
class Playbook:
    playbook_id: str
    title: str
    macro: str | None = None
    structured_output_schema: dict[str, Any] | None = None


@dataclass(frozen=True)
class SessionRequest:
    prompt: str
    title: str
    playbook_id: str
    max_acu_limit: int
    tags: tuple[str, ...] = ()
    repos: tuple[str, ...] = (FORK_REPO,)
    structured_output_schema: dict[str, Any] | None = None
    structured_output_required: bool = True
    devin_mode: str | None = None

    def to_payload(self) -> dict[str, Any]:
        """Body for POST /v3/organizations/{org_id}/sessions. Optional fields are omitted when unset."""
        payload: dict[str, Any] = {
            "prompt": self.prompt,
            "title": self.title,
            "playbook_id": self.playbook_id,
            "max_acu_limit": self.max_acu_limit,
            "tags": list(self.tags),
            "repos": list(self.repos),
            "structured_output_required": self.structured_output_required,
        }
        if self.structured_output_schema is not None:
            payload["structured_output_schema"] = self.structured_output_schema
        if self.devin_mode:
            payload["devin_mode"] = self.devin_mode
        return payload


@dataclass(frozen=True)
class SessionInfo:
    """Subset of GET /v3/organizations/{org_id}/sessions/{devin_id}."""

    session_id: str
    status: str
    url: str = ""
    status_detail: str | None = None
    pull_requests: tuple[PullRequestRef, ...] = ()
    acus_consumed: float = 0.0
    structured_output: dict[str, Any] | None = None
    tags: tuple[str, ...] = ()
    updated_at: float | None = None

    @property
    def pr_urls(self) -> tuple[str, ...]:
        return tuple(pr.pr_url for pr in self.pull_requests if pr.pr_url)


@dataclass(frozen=True)
class CveFinding:
    cve_id: str
    verdict: Verdict
    confidence: Confidence | None = None
    evidence: tuple[str, ...] = ()  # "file:line — description"
    notes: str = ""

    @property
    def effective_confidence(self) -> Confidence:
        """Missing confidence counts as low (docs/PLAN.md §9)."""
        return self.confidence or Confidence.LOW


@dataclass(frozen=True)
class TriageResult:
    issue_number: int
    cves: tuple[CveFinding, ...]
    package: str = ""
    installed_version: str = ""
    head_sha: str = ""
    comment_url: str = ""
    caveats: tuple[str, ...] = ()


@dataclass(frozen=True)
class IssueFacts:
    """Parsed from the issue body (`Package:`, `Current -> fixed:`, `CVEs:` lines)."""

    package: str | None
    current_version: str | None
    fixed_version: str | None
    bump_kind: BumpKind
    cve_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RouteDecision:
    action: RouteAction
    reason: str
    qualifying: tuple[CveFinding, ...] = ()
    others: tuple[CveFinding, ...] = field(default_factory=tuple)
    unexpected: tuple[str, ...] = ()
