"""Periodic and on-demand GitHub issue sweeps."""

from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.github_client import GitHubError
from app.interfaces import Deps
from app.request_security import is_allowed_local_request

logger = logging.getLogger(__name__)


async def run_sweep(deps: Deps, issue_number: int | None = None) -> dict:
    if issue_number is None:
        try:
            issues = await deps.github.list_open_issues_with_label(deps.settings.trigger_label)
        except GitHubError as err:
            logger.warning("%s", str(err))
            return {"error": "github_unavailable"}
    else:
        try:
            issue = await deps.github.get_issue(issue_number)
        except GitHubError as err:
            logger.warning("%s", str(err))
            return {"error": "issue_not_found" if err.status_code == 404 else "github_unavailable"}
        if issue.state != "open" or issue.is_pull_request or deps.settings.trigger_label not in issue.labels:
            return {"found": 0, "new": 0, "new_issues": []}
        issues = [issue]

    new_issues: list[int] = []
    found = 0
    now = deps.clock()
    for issue in issues:
        if issue.is_pull_request:
            continue
        found += 1
        if deps.db.upsert_seen_issue(issue, now):
            new_issues.append(issue.number)
            deps.db.add_event(issue.number, "sweep", "seen via sweep", now)
    if new_issues:
        deps.wake.set()
    return {"found": found, "new": len(new_issues), "new_issues": new_issues}


def register(app: FastAPI, deps: Deps) -> None:
    async def sweep_loop() -> None:
        while True:
            try:
                await run_sweep(deps)
            except Exception:
                logger.exception("Unexpected GitHub sweep failure")
            await asyncio.sleep(deps.settings.sweep_interval_s)

    deps.background.append(sweep_loop)

    @app.post("/sweep")
    async def sweep_now(request: Request, issue: str | None = None) -> JSONResponse:
        if not is_allowed_local_request(request, deps.settings.sweep_allowed_cidrs):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        issue_number = None
        if issue is not None:
            try:
                issue_number = int(issue)
            except ValueError:
                return JSONResponse({"error": "invalid issue"}, status_code=400)
            if issue_number <= 0:
                return JSONResponse({"error": "invalid issue"}, status_code=400)
        result = await run_sweep(deps, issue_number)
        status_code = {
            "issue_not_found": 404,
            "github_unavailable": 502,
        }.get(result.get("error"), 200)
        return JSONResponse(result, status_code=status_code)
