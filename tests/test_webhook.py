import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from app.config import Settings
from app.fake_github import FakeGitHub
from app.main import create_app
from app.models import IssueState

SECRET = "test-only-webhook-secret"


def _payload(
    issue_number: int = 1,
    *,
    label: str = "devin:fixplease",
    repo: str = "dmonroym0/superset",
    pull_request: bool = False,
) -> bytes:
    issue = {
        "number": issue_number,
        "title": "Demo issue",
        "body": "Untrusted issue text",
        "labels": [{"name": label}],
        "state": "open",
        "html_url": f"https://demo.invalid/issues/{issue_number}",
    }
    if pull_request:
        issue["pull_request"] = {"url": "https://demo.invalid/pull/1"}
    payload = {
        "action": "labeled",
        "label": {"name": label},
        "issue": issue,
        "repository": {"full_name": repo},
    }
    return json.dumps(payload).encode()


def _headers(body: bytes, *, delivery: str = "delivery-test", event: str = "issues") -> dict[str, str]:
    signature = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return {
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery,
        "X-Hub-Signature-256": f"sha256={signature}",
    }


def _app(tmp_path, fake_devin, **env):
    settings = Settings.from_env(
        {
            "DB_PATH": str(tmp_path / "webhook.db"),
            "GITHUB_WEBHOOK_SECRET": SECRET,
            **env,
        }
    )
    app = create_app(settings, github=FakeGitHub.from_seed(), devin=fake_devin, clock=lambda: 100.0)
    app.state.deps.background.clear()
    return app


def test_valid_webhook_is_deduped_and_persists_issue(tmp_path, fake_devin):
    app = _app(tmp_path, fake_devin)
    body = _payload(1)
    headers = _headers(body)
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        response = client.post("/webhooks/github", content=body, headers=headers)
        duplicate = client.post("/webhooks/github", content=body, headers=headers)

        assert response.status_code == 202
        assert response.json() == {"status": "accepted", "issue": 1, "new": True}
        assert duplicate.status_code == 200
        assert duplicate.json() == {"status": "duplicate"}
        assert app.state.deps.db.get_issue(1).state is IssueState.SEEN
        accepted = [event for event in app.state.deps.db.list_events() if event.kind == "webhook_accepted"]
        assert len(accepted) == 1


def test_webhook_rejects_signatures_and_oversized_payloads(tmp_path, fake_devin):
    app = _app(tmp_path, fake_devin)
    body = _payload(5)
    headers = _headers(body)
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        assert (
            client.post(
                "/webhooks/github",
                content=body,
                headers={**headers, "X-Hub-Signature-256": "sha256=bad"},
            ).status_code
            == 401
        )
        missing_signature = dict(headers)
        del missing_signature["X-Hub-Signature-256"]
        assert client.post("/webhooks/github", content=body, headers=missing_signature).status_code == 401
        oversized = b"x" * 1_048_577
        response = client.post("/webhooks/github", content=oversized, headers=_headers(oversized))
        assert response.status_code == 413
        assert response.json() == {"error": "payload too large"}


def test_chunked_oversized_webhook_is_rejected(tmp_path, fake_devin):
    app = _app(tmp_path, fake_devin)

    def body_chunks():
        for _ in range(18):
            yield b"x" * (64 * 1024)

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        response = client.post(
            "/webhooks/github",
            content=body_chunks(),
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 413
    assert response.json() == {"error": "payload too large"}


async def test_chunked_oversized_webhook_stops_reading_at_limit(tmp_path, fake_devin):
    app = _app(tmp_path, fake_devin)
    chunks_read = 0
    response_messages = []

    async def receive():
        nonlocal chunks_read
        chunks_read += 1
        return {
            "type": "http.request",
            "body": b"x" * (64 * 1024),
            "more_body": chunks_read < 18,
        }

    async def send(message):
        response_messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/webhooks/github",
        "raw_path": b"/webhooks/github",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }

    await app(scope, receive, send)

    assert response_messages[0]["status"] == 413
    assert chunks_read == 17


def test_webhook_ignores_other_events_labels_repositories_and_pull_requests(tmp_path, fake_devin):
    app = _app(tmp_path, fake_devin)
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        ping = b"{}"
        assert client.post("/webhooks/github", content=ping, headers=_headers(ping, event="ping")).json() == {
            "status": "pong"
        }
        cases = [
            (_payload(1, label="security"), "label"),
            (_payload(1, repo="dmonroym0/devin-superset-demo"), "repository"),
            (_payload(1, pull_request=True), "pull_request"),
        ]
        for index, (body, reason) in enumerate(cases):
            response = client.post(
                "/webhooks/github",
                content=body,
                headers=_headers(body, delivery=f"ignored-{index}"),
            )
            assert response.status_code == 200
            assert response.json() == {"status": "ignored", "reason": reason}
        assert app.state.deps.db.get_issue(1) is None


def test_missing_delivery_id_and_live_disabled_webhook(tmp_path, fake_devin):
    app = _app(tmp_path, fake_devin)
    body = _payload(5)
    headers = _headers(body)
    del headers["X-GitHub-Delivery"]
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        response = client.post("/webhooks/github", content=body, headers=headers)
        assert response.status_code == 400
        assert response.json() == {"error": "missing delivery id"}

    live = create_app(
        Settings.from_env({"APP_MODE": "live", "DB_PATH": str(tmp_path / "live.db")}),
        github=FakeGitHub.from_seed(),
        devin=fake_devin,
    )
    with TestClient(live, client=("127.0.0.1", 50000)) as client:
        response = client.post("/webhooks/github", content=body, headers=_headers(body))
        assert response.status_code == 503
        assert response.json() == {"error": "webhook disabled"}
