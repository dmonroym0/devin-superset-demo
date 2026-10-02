import threading
from copy import deepcopy
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import MANAGED_LABELS
from app.playbooks import SchemaMismatch


def test_health_metrics_labels_and_lifecycle(fake_github, fake_devin, test_settings):
    app = create_app(test_settings, github=fake_github, devin=fake_devin, clock=lambda: 100.0)
    startup_ran = threading.Event()
    background_cancelled = threading.Event()

    async def startup_hook():
        startup_ran.set()

    async def background_worker():
        import asyncio

        try:
            await asyncio.Event().wait()
        finally:
            background_cancelled.set()

    app.state.deps.startup.append(startup_hook)
    app.state.deps.background.append(background_worker)

    with TestClient(app) as client:
        assert startup_ran.wait(timeout=1)
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json() == {"status": "ok", "mode": "demo"}
        metrics = client.get("/metrics.json")
        assert metrics.status_code == 200
        payload = metrics.json()
        assert set(payload) == {
            "generated_at",
            "mode",
            "issues",
            "automation_rate",
            "median_time_to_pr_s",
            "acu",
            "sessions",
            "issues_detail",
        }
        assert set(payload["issues"]) == {
            "seen",
            "in_flight",
            "pr_opened",
            "needs_human",
            "not_reachable",
            "queued_budget",
            "error",
        }
        assert set(payload["acu"]) == {"committed", "ceiling", "remaining", "consumed_metered"}

    assert fake_github.ensure_labels_calls == [MANAGED_LABELS]
    assert background_cancelled.wait(timeout=1)
    assert fake_github.closed
    assert fake_devin.closed


def test_label_setup_failure_does_not_prevent_startup(fake_github, fake_devin, test_settings):
    async def failing_ensure_labels(labels):
        raise RuntimeError("hidden-token-value")

    fake_github.ensure_labels = failing_ensure_labels
    app = create_app(test_settings, github=fake_github, devin=fake_devin)

    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200


def test_demo_startup_rejects_triage_schema_mismatch(fake_github, fake_devin, test_settings):
    triage = fake_devin.playbooks[0]
    schema = deepcopy(triage.structured_output_schema)
    schema["properties"]["cves"]["items"]["properties"]["confidence"]["enum"].append("unexpected")
    fake_devin.playbooks[0] = replace(triage, structured_output_schema=schema)
    app = create_app(test_settings, github=fake_github, devin=fake_devin)

    with (
        pytest.raises(
            SchemaMismatch,
            match=r"properties\.cves\.items\.properties\.confidence\.enum",
        ),
        TestClient(app),
    ):
        pass
