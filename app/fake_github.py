"""In-memory GitHub client seeded with the demo issues."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from app.github_client import GitHubError
from app.models import (
    FORK_REPO,
    CompareResult,
    Issue,
    LabelSpec,
    MergeUpstreamResult,
    UpstreamCommit,
)


class FakeGitHub:
    def __init__(self, issues: Sequence[Issue] = (), *, upstream_scenario: str = "merge"):
        self.issues = {issue.number: issue for issue in issues}
        self.comments: dict[int, list[str]] = {}
        self.closed: dict[int, str] = {}
        self.created_issues: list[Issue] = []
        self.created_prs: list[dict[str, str]] = []
        self.existing_labels = {label for issue in issues for label in issue.labels}
        self._comment_counter = 0
        upstream_path = Path(__file__).parent / "demo" / "upstream.json"
        self._upstream = json.loads(upstream_path.read_text())[upstream_scenario]
        self._upstream_merged = False
        self._changelog_files: dict[str, str] = {}
        self._pull_counter = 899

    @classmethod
    def from_seed(
        cls,
        path: Path = Path(__file__).parent / "demo" / "seed_issues.json",
        *,
        upstream_scenario: str = "merge",
    ) -> FakeGitHub:
        seed = json.loads(path.read_text())
        issues = [
            Issue(
                number=item["number"],
                title=item["title"],
                body=item.get("body") or "",
                labels=tuple(item.get("labels", [])),
                state=item.get("state", "open"),
                html_url=item.get("html_url", ""),
                repo=seed.get("repo", FORK_REPO),
            )
            for item in seed["issues"]
        ]
        return cls(issues, upstream_scenario=upstream_scenario)

    def labels(self, number: int) -> list[str]:
        return list(self._get_issue(number, "GET", f"/issues/{number}").labels)

    def add_issue(self, issue: Issue) -> None:
        self.issues[issue.number] = issue
        self.existing_labels.update(issue.labels)

    def ensure_issue_labeled(self, issue: Issue, label: str) -> None:
        existing = self.issues.get(issue.number)
        if existing is None:
            labels = tuple(dict.fromkeys((*issue.labels, label)))
            self.issues[issue.number] = replace(issue, labels=labels)
        elif label not in existing.labels:
            self.issues[issue.number] = replace(existing, labels=(*existing.labels, label))
        self.existing_labels.update(self.issues[issue.number].labels)

    def _get_issue(self, number: int, method: str, path: str) -> Issue:
        try:
            return self.issues[number]
        except KeyError as err:
            raise GitHubError(404, method, path) from err

    async def list_open_issues_with_label(self, label: str) -> list[Issue]:
        return [issue for issue in self.issues.values() if issue.state == "open" and label in issue.labels]

    async def get_issue(self, number: int) -> Issue:
        return self._get_issue(number, "GET", f"/issues/{number}")

    async def ensure_labels(self, specs: Sequence[LabelSpec]) -> list[str]:
        created = [spec.name for spec in specs if spec.name not in self.existing_labels]
        self.existing_labels.update(created)
        return created

    async def add_labels(self, number: int, labels: Sequence[str]) -> None:
        issue = self._get_issue(number, "POST", f"/issues/{number}/labels")
        combined = tuple(dict.fromkeys((*issue.labels, *labels)))
        self.issues[number] = replace(issue, labels=combined)
        self.existing_labels.update(labels)

    async def remove_label(self, number: int, label: str) -> None:
        issue = self._get_issue(number, "DELETE", f"/issues/{number}/labels/{label}")
        self.issues[number] = replace(
            issue, labels=tuple(existing for existing in issue.labels if existing != label)
        )

    async def create_comment(self, number: int, body: str) -> str:
        self._get_issue(number, "POST", f"/issues/{number}/comments")
        self.comments.setdefault(number, []).append(body)
        self._comment_counter += 1
        return f"https://demo.invalid/{FORK_REPO}/issues/{number}#comment-{self._comment_counter}"

    async def close_issue(self, number: int, reason: str = "not_planned") -> None:
        issue = self._get_issue(number, "PATCH", f"/issues/{number}")
        self.closed[number] = reason
        self.issues[number] = replace(issue, state="closed")

    async def create_issue(self, title: str, body: str, labels: Sequence[str]) -> Issue:
        number = max(self.issues, default=0) + 1
        issue = Issue(
            number=number,
            title=title,
            body=body,
            labels=tuple(dict.fromkeys(labels)),
            html_url=f"https://demo.invalid/{FORK_REPO}/issues/{number}",
        )
        self.add_issue(issue)
        self.created_issues.append(issue)
        return issue

    async def get_branch_sha(self, branch: str) -> str:
        del branch
        key = "after_sha" if self._upstream_merged else "before_sha"
        return self._upstream[key]

    async def merge_upstream(self, branch: str) -> MergeUpstreamResult:
        del branch
        if self._upstream.get("outcome") == "conflict":
            return MergeUpstreamResult("conflict", "simulated merge conflict")
        if self._upstream_merged:
            return MergeUpstreamResult("none", "Already up to date")
        self._upstream_merged = True
        outcome = self._upstream.get("outcome", "merged")
        return MergeUpstreamResult(outcome, "Simulated upstream merge")

    async def compare(self, base: str, head: str) -> CompareResult:
        del base, head
        return CompareResult(
            commits=tuple(
                UpstreamCommit(
                    sha=item["sha"],
                    subject=item["subject"],
                    is_merge=item.get("is_merge", False),
                )
                for item in self._upstream["commits"]
            ),
            files=tuple(self._upstream["files"]),
        )

    async def get_file(self, path: str, ref: str) -> str | None:
        if path != "FORK_CHANGELOG.md":
            return None
        return self._changelog_files.get(ref)

    async def create_changelog_pr(
        self,
        base_branch: str,
        head_sha: str,
        branch_name: str,
        content: str,
        title: str,
        body: str,
    ) -> str:
        del head_sha
        self._pull_counter += 1
        self._changelog_files[branch_name] = content
        self.created_prs.append(
            {
                "base_branch": base_branch,
                "branch_name": branch_name,
                "content": content,
                "title": title,
                "body": body,
            }
        )
        return f"https://github.com/{FORK_REPO}/pull/{self._pull_counter}"

    async def aclose(self) -> None:
        return None
