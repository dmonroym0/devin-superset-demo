import importlib.util
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.fake_github import FakeGitHub
from app.main import create_app

SCRIPT = Path(__file__).parents[1] / "scripts" / "simulate_webhook.py"
SPEC = importlib.util.spec_from_file_location("simulate_webhook", SCRIPT)
SIMULATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SIMULATE)
SECRET = "test-only-simulator-secret"


def _app(tmp_path, fake_devin):
    return create_app(
        Settings.from_env({"DB_PATH": str(tmp_path / "simulate.db"), "GITHUB_WEBHOOK_SECRET": SECRET}),
        github=FakeGitHub.from_seed(),
        devin=fake_devin,
    )


def test_signed_simulation_payload_is_accepted(tmp_path, fake_devin):
    app = _app(tmp_path, fake_devin)
    body = SIMULATE.build_payload(1)
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        response = client.post(
            "/webhooks/github",
            content=body,
            headers={
                "X-GitHub-Event": "issues",
                "X-GitHub-Delivery": "simulation-test",
                "X-Hub-Signature-256": SIMULATE.sign(SECRET, body),
            },
        )
        assert response.status_code == 202


def test_bad_signature_and_unseeded_issue_payload(tmp_path, fake_devin):
    app = _app(tmp_path, fake_devin)
    body = SIMULATE.build_payload(9001)
    payload = json.loads(body)
    assert payload["issue"]["number"] == 9001
    assert payload["repository"]["full_name"] == "dmonroym0/superset"
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        response = client.post(
            "/webhooks/github",
            content=body,
            headers={
                "X-GitHub-Event": "issues",
                "X-GitHub-Delivery": "simulation-bad-signature",
                "X-Hub-Signature-256": SIMULATE.sign("wrong-test-secret", body),
            },
        )
        assert response.status_code == 401
