"""Periodic and on-demand GitHub issue sweeps."""

from __future__ import annotations

import asyncio
import ipaddress
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.github_client import GitHubError
from app.interfaces import Deps

logger = logging.getLogger(__name__)


async def run_sweep(deps: Deps) -> dict:
    try:
        issues = await deps.github.list_open_issues_with_label(deps.settings.trigger_label)
    except GitHubError as err:
        logger.warning("%s", str(err))
        return {"error": "github_unavailable"}

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
    async def sweep_now(request: Request) -> JSONResponse:
        if "x-forwarded-for" in request.headers or "forwarded" in request.headers:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        if request.client is None:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        try:
            address = ipaddress.ip_address(request.client.host)
            allowed = any(
                address in ipaddress.ip_network(cidr, strict=False)
                for cidr in deps.settings.sweep_allowed_cidrs
            )
        except ValueError:
            allowed = False
        if not allowed:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        result = await run_sweep(deps)
        return JSONResponse(result, status_code=502 if "error" in result else 200)
