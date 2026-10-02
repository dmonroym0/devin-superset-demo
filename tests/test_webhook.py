import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from app.config import Settings
from app.fake_github import FakeGitHub
from app.main import create_app
from app.models import Issue, IssueState

SECRET = "test-only-webhook-secret"


def _payload(
    issue_number: int = 1,
    *,
    label: str = "devin:fixplease",
    repo: str = "dmonroym0/superset",
    pull_request: bool = False,
    state: str = "open",
) -> bytes:
    issue = {
        "number": issue_number,
        "title": "Demo issue",
        "body": "Untrusted issue text",
        "labels": [{"name": label}],
        "state": state,
        "html_url": f"https://demo.invalid/issues/{issue_number}",
    }
    if pull_request:
        issue["pull_request"] = {"url": "https://github.com/dmonroym0/superset/pull/1"}
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


def test_relabelled_cancelled_issue_is_revived_by_webhook(tmp_path, fake_devin):
    app = _app(tmp_path, fake_devin)
    body = _payload(77)

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        db = app.state.deps.db
        db.upsert_seen_issue(Issue(number=77, title="Cancelled issue", body=""), 90.0)
        db.transition(
            77,
            [IssueState.SEEN],
            IssueState.CANCELLED,
            95.0,
            route_reason="trigger label removed",
        )
        response = client.post(
            "/webhooks/github",
            content=body,
            headers=_headers(body, delivery="relabel-cancelled-77"),
        )
        assert response.status_code == 202
        assert response.json() == {"status": "accepted", "issue": 77, "new": True}
        row = db.get_issue(77)
        assert row.state is IssueState.SEEN
        assert row.route_reason is None
        assert "devin:fixplease" in app.state.deps.github.issues[77].labels


def test_demo_webhook_keeps_existing_closed_issue_state(tmp_path, fake_devin):
    app = _app(tmp_path, fake_devin)
    github = app.state.deps.github
    github.add_issue(
        Issue(
            number=1,
            title="Existing closed issue",
            body="Original fake state",
            labels=("devin:in-progress",),
            state="closed",
        )
    )
    body = _payload(1, state="open")

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        response = client.post(
            "/webhooks/github",
            content=body,
            headers=_headers(body, delivery="delayed-open-snapshot"),
        )

    assert response.status_code == 202
    issue = github.issues[1]
    assert issue.state == "closed"
    assert "devin:in-progress" in issue.labels
    assert "devin:fixplease" in issue.labels


def test_demo_webhook_adds_trigger_label_to_unknown_issue(tmp_path, fake_devin):
    app = _app(tmp_path, fake_devin)
    github = app.state.deps.github
    body = _payload(9876)

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        response = client.post(
            "/webhooks/github",
            content=body,
            headers=_headers(body, delivery="new-demo-issue"),
        )

    assert response.status_code == 202
    assert github.issues[9876].state == "open"
    assert "devin:fixplease" in github.issues[9876].labels


def test_failed_webhook_accept_can_retry_same_delivery(tmp_path, fake_devin):
    app = _app(tmp_path, fake_devin)
    db = app.state.deps.db
    body = _payload(7)
    headers = _headers(body, delivery="delivery-retry-after-failure")
    with TestClient(
        app,
        client=("127.0.0.1", 50000),
        raise_server_exceptions=False,
    ) as client:
        db._connection.execute(
            "CREATE TRIGGER fail_issue BEFORE INSERT ON issues BEGIN SELECT RAISE(ABORT, 'boom'); END"
        )
        db._connection.commit()
        first = client.post("/webhooks/github", content=body, headers=headers)
        assert first.status_code >= 500
        assert db.has_delivery("delivery-retry-after-failure") is False
        assert db.get_issue(7) is None
        assert db.list_events(issue_number=7) == []

        db._connection.execute("DROP TRIGGER fail_issue")
        db._connection.commit()
        retry = client.post("/webhooks/github", content=body, headers=headers)
        assert retry.status_code == 202
        assert retry.json() == {"status": "accepted", "issue": 7, "new": True}
        duplicate = client.post("/webhooks/github", content=body, headers=headers)
        assert duplicate.status_code == 200
        assert duplicate.json() == {"status": "duplicate"}


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


def test_webhook_streams_oversized_chunked_payload(tmp_path, fake_devin):
    app = _app(tmp_path, fake_devin)
    chunks = (chunk for chunk in (b"x" * 600_000, b"x" * 600_000))
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        response = client.post("/webhooks/github", content=chunks)
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


def test_webhook_ignores_closed_labeled_issue(tmp_path, fake_devin):
    app = _app(tmp_path, fake_devin)
    body = _payload(6, state="closed")
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        response = client.post("/webhooks/github", content=body, headers=_headers(body))
        assert response.status_code == 200
        assert response.json() == {"status": "ignored", "reason": "closed"}
        assert app.state.deps.db.get_issue(6) is None


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
