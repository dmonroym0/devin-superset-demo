"""Undo what the service did to fork issues between demo takes. Dry run unless --apply.

The service (app/issue_actions.py) adds devin:* status labels, posts comments, and closes
not-reachable issues as "not_planned" after adding devin:low-priority. This script removes those
labels, deletes those comments, and reopens issues the service closed. It only touches the issue
numbers passed in. Pass --recreate to also open a fresh copy with the same title and body; the
original then loses devin:fixplease so the service does not pick it up again.
"""

import argparse
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from urllib.parse import quote

import httpx

DEFAULT_REPO = "dmonroym0/superset"
TRIGGER_LABEL = "devin:fixplease"
LOW_PRIORITY_LABEL = "devin:low-priority"
SERVICE_LABELS = (
    "devin:in-progress",
    "devin:pr-opened",
    "devin:needs-human",
    LOW_PRIORITY_LABEL,
    "devin:queued-budget",
    "devin:triage-rejected",
)
SERVICE_COMMENT_TEMPLATES = (
    re.compile(r"### devin-superset-demo triage\n.*\n_Automated by devin-superset-demo\._", re.DOTALL),
    re.compile(r"(?:- [^\n]*\n)+\nOpened, not done: CI and review continue in the Devin session\."),
    re.compile(r"Needs a human: [^\n]*"),
    re.compile(r"Queued for the next retry: committed \d+ / ceiling \d+ ACUs\."),
)


@dataclass
class Plan:
    number: int
    title: str
    body: str
    remove_labels: list[str] = field(default_factory=list)
    reopen: bool = False
    delete_comments: list[tuple[int, str]] = field(default_factory=list)
    copy_labels: list[str] = field(default_factory=list)


def is_service_comment(comment: Mapping, actor: str) -> bool:
    body = comment.get("body") or ""
    author = (comment.get("user") or {}).get("login")
    return author == actor and any(template.fullmatch(body) for template in SERVICE_COMMENT_TEMPLATES)


def build_plan(
    issue: Mapping,
    comments: Sequence[Mapping],
    actor: str,
    *,
    remove_trigger: bool,
    keep_comments: bool,
    recreate: bool = False,
) -> Plan:
    labels = [label["name"] for label in issue.get("labels", [])]
    removable = set(SERVICE_LABELS) | ({TRIGGER_LABEL} if remove_trigger or recreate else set())
    closed_by = (issue.get("closed_by") or {}).get("login")
    plan = Plan(number=issue["number"], title=issue.get("title") or "", body=issue.get("body") or "")
    plan.remove_labels = [name for name in labels if name in removable]
    plan.reopen = (
        issue.get("state") == "closed"
        and issue.get("state_reason") == "not_planned"
        and closed_by == actor
        and LOW_PRIORITY_LABEL in labels
    )
    if not keep_comments:
        plan.delete_comments = [
            (comment["id"], (comment.get("body") or "").splitlines()[0][:60])
            for comment in comments
            if is_service_comment(comment, actor)
        ]
    plan.copy_labels = [name for name in labels if name not in SERVICE_LABELS and name != TRIGGER_LABEL]
    return plan


def _paginate(client: httpx.Client, path: str) -> list[dict]:
    items: list[dict] = []
    url: str | None = path
    params: dict[str, int] | None = {"per_page": 100}
    while url:
        response = client.get(url, params=params)
        response.raise_for_status()
        items.extend(response.json())
        url = response.links.get("next", {}).get("url")
        params = None
    return items


def apply_plan(client: httpx.Client, repo: str, plan: Plan, *, recreate: bool) -> str | None:
    issue_path = f"/repos/{repo}/issues/{plan.number}"
    if plan.reopen:
        client.patch(issue_path, json={"state": "open"}).raise_for_status()
    for name in plan.remove_labels:
        response = client.delete(f"{issue_path}/labels/{quote(name, safe='')}")
        if response.status_code != 404:
            response.raise_for_status()
    for comment_id, _ in plan.delete_comments:
        response = client.delete(f"/repos/{repo}/issues/comments/{comment_id}")
        if response.status_code != 404:
            response.raise_for_status()
    if not recreate:
        return None
    response = client.post(
        f"/repos/{repo}/issues",
        json={"title": plan.title, "body": plan.body, "labels": plan.copy_labels},
    )
    response.raise_for_status()
    return response.json().get("html_url")


def describe(plan: Plan, *, recreate: bool) -> list[str]:
    lines = [f"#{plan.number} {plan.title}"]
    if plan.reopen:
        lines.append("  reopen (closed as not_planned by the service)")
    lines += [f"  remove label {name}" for name in plan.remove_labels]
    lines += [f"  delete service comment {cid}: {first}" for cid, first in plan.delete_comments]
    if recreate:
        lines.append(f"  create fresh copy (same title and body, labels {plan.copy_labels or 'none'})")
    if len(lines) == 1:
        lines.append("  nothing to undo")
    return lines


def main(
    argv: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("issues", nargs="+", type=int, help="fork issue numbers to reset")
    parser.add_argument("--apply", action="store_true", help="make the changes (default: dry run)")
    parser.add_argument(
        "--recreate",
        action="store_true",
        help=f"also open a fresh copy of each issue and remove {TRIGGER_LABEL} from the original",
    )
    parser.add_argument("--remove-trigger", action="store_true", help=f"also remove {TRIGGER_LABEL}")
    parser.add_argument("--keep-comments", action="store_true", help="do not delete service comments")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    args = parser.parse_args(argv)
    env = os.environ if env is None else env

    token = env.get("GITHUB_TOKEN", "")
    if not token:
        print("GITHUB_TOKEN: missing", file=sys.stderr)
        return 2
    client = httpx.Client(
        base_url=env.get("GITHUB_API_BASE", "https://api.github.com"),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "devin-superset-demo-reset",
        },
        timeout=20,
    )
    try:
        with client:
            user = client.get("/user")
            user.raise_for_status()
            actor = user.json()["login"]
            print(f"{'APPLY' if args.apply else 'DRY RUN'} on {args.repo} as {actor}")
            for number in dict.fromkeys(args.issues):
                issue = client.get(f"/repos/{args.repo}/issues/{number}")
                issue.raise_for_status()
                data = issue.json()
                if "pull_request" in data:
                    print(f"#{number}: is a pull request, skipped")
                    continue
                comments = _paginate(client, f"/repos/{args.repo}/issues/{number}/comments")
                plan = build_plan(
                    data,
                    comments,
                    actor,
                    remove_trigger=args.remove_trigger,
                    keep_comments=args.keep_comments,
                    recreate=args.recreate,
                )
                print("\n".join(describe(plan, recreate=args.recreate)))
                if args.apply:
                    url = apply_plan(client, args.repo, plan, recreate=args.recreate)
                    if url:
                        print(f"  created {url}")
    except httpx.HTTPStatusError as err:
        print(
            f"GitHub {err.request.method} {err.request.url.path} -> {err.response.status_code}",
            file=sys.stderr,
        )
        return 1
    except httpx.HTTPError as err:
        print(f"GitHub request failed: {type(err).__name__}", file=sys.stderr)
        return 1
    if not args.apply:
        print("dry run: nothing changed; rerun with --apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
