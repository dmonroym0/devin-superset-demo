"""Rendering helpers for fork upstream-sync changelogs."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date

from app.issue_actions import _escape
from app.models import UpstreamCommit

_HEADER = "# Fork changelog"
_CONVENTIONAL = re.compile(r"^(?P<type>[a-z]+)(\([^)]*\))?(?P<bang>!)?: ")
_COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
_REQUIREMENTS_FILE = re.compile(r"^requirements/[^/]+\.txt$")
_GROUP_ORDER = (
    "Breaking changes",
    "feat",
    "fix",
    "perf",
    "refactor",
    "docs",
    "test",
    "build",
    "ci",
    "chore",
    "revert",
    "Other",
)


def _commit_group(commit: UpstreamCommit) -> str:
    match = _CONVENTIONAL.match(commit.subject)
    if (match and match.group("bang")) or "BREAKING CHANGE" in commit.subject:
        return "Breaking changes"
    if match and match.group("type") in _GROUP_ORDER:
        return match.group("type")
    return "Other"


def included_commits(commits: tuple[UpstreamCommit, ...]) -> tuple[UpstreamCommit, ...]:
    return tuple(
        commit
        for commit in commits
        if not commit.is_merge and not commit.subject.startswith("docs(fork-changelog):")
    )


def grouped_commits(commits: tuple[UpstreamCommit, ...]) -> dict[str, tuple[UpstreamCommit, ...]]:
    groups: defaultdict[str, list[UpstreamCommit]] = defaultdict(list)
    for commit in included_commits(commits):
        groups[_commit_group(commit)].append(commit)
    return {group: tuple(groups[group]) for group in _GROUP_ORDER if groups[group]}


def render_section(
    commits: tuple[UpstreamCommit, ...],
    files: tuple[str, ...],
    base_sha: str,
    head_sha: str,
    synced_on: date,
) -> str:
    lines = [f"## Upstream sync {synced_on.isoformat()} ({base_sha[:7]}..{head_sha[:7]})"]
    for group, entries in grouped_commits(commits).items():
        lines.extend(["", f"### {group}"])
        for commit in entries:
            subject = _escape(commit.subject)
            short_sha = _escape(commit.sha[:7])
            if _COMMIT_SHA.fullmatch(commit.sha):
                reference = f"[{short_sha}](https://github.com/apache/superset/commit/{commit.sha})"
            else:
                reference = short_sha
            lines.append(f"- {subject} ({reference})")

    requirement_files = [path for path in files if _REQUIREMENTS_FILE.fullmatch(path)]
    lines.extend(["", "### Dependency changes"])
    if requirement_files:
        lines.extend(f"- {_escape(path)}" for path in requirement_files)
    else:
        lines.append("_No requirements/*.txt changes._")
    return "\n".join(lines)


def prepend(existing: str | None, section: str) -> str:
    existing_lines = (existing or "").splitlines()
    old_content = "\n".join(line for line in existing_lines if line != _HEADER).strip()
    heading = section.splitlines()[0] if section.splitlines() else ""
    if heading and heading in old_content.splitlines():
        content = old_content
    elif old_content:
        content = f"{section.strip()}\n\n{old_content}"
    else:
        content = section.strip()
    return f"{_HEADER}\n\n{content}\n"
