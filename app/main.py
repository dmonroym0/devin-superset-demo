"""FastAPI application factory and process lifecycle."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import dashboard, devin_client, github_client, pipeline, sweep, webhook
from app.budget import Budget
from app.config import Settings
from app.db import Database
from app.interfaces import Clock, Deps, DevinClient, GitHubClient
from app.metrics import compute
from app.models import MANAGED_LABELS

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    github: GitHubClient | None = None,
    devin: DevinClient | None = None,
    clock: Clock = time.time,
) -> FastAPI:
    settings = settings or Settings.from_env()
    github = github if github is not None else github_client.build_github_client(settings)
    devin = devin if devin is not None else devin_client.build_devin_client(settings)
    db = Database(settings.db_path)
    budget = Budget(db, settings.acu_ceiling)
    deps = Deps(settings=settings, db=db, budget=budget, github=github, devin=devin, clock=clock)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        tasks: list[asyncio.Task] = []
        try:
            deps.db.init_schema()
            deps.db.claim_mode(settings.mode.value)
            try:
                created_labels = await deps.github.ensure_labels(MANAGED_LABELS)
                if created_labels:
                    logger.info("Created managed GitHub labels: %s", ", ".join(created_labels))
            except Exception:  # noqa: BLE001
                logger.warning("Unable to ensure managed GitHub labels; continuing")
            for startup_factory in deps.startup:
                await startup_factory()
            tasks = [asyncio.create_task(background_factory()) for background_factory in deps.background]
            yield
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.gather(deps.github.aclose(), deps.devin.aclose(), return_exceptions=True)
            deps.db.close()

    app = FastAPI(lifespan=lifespan)
    app.state.deps = deps

    for module in (webhook, sweep, pipeline, dashboard):
        module.register(app, deps)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "mode": settings.mode.value}

    @app.get("/metrics.json")
    async def metrics() -> dict:
        return compute(deps.db, deps.budget, settings, now=clock())

    return app
