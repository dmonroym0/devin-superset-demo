import json
import re
import time

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import (
    LABEL_LOW_PRIORITY,
    LABEL_NEEDS_HUMAN,
    LABEL_PR_OPENED,
    LABEL_QUEUED_BUDGET,
    LABEL_TRIAGE_REJECTED,
    MANAGED_LABELS,
    Issue,
    IssueState,
    Stage,
)
from scripts.simulate_webhook import build_payload, sign

DEMO_SECRET = "demo-only-not-a-secret"
REJECTED_PR_URL = "https://demo.invalid/dmonroym0/superset/pull/9001"


def _settings(db_path, **env):
    return Settings.from_env({"APP_MODE": "demo", "DB_PATH": str(db_path), **env})


def _post_webhook(
    client,
    issue_number: int,
    delivery_id: str,
    *,
    repo: str = "dmonroym0/superset",
    bad_signature: bool = False,
    body: bytes | None = None,
):
    body = body or build_payload(issue_number, repo=repo)
    signature = sign("wrong-secret" if bad_signature else DEMO_SECRET, body)
    return client.post(
        "/webhooks/github",
        content=body,
        headers={
            "content-type": "application/json",
            "x-github-delivery": delivery_id,
            "x-github-event": "issues",
            "x-hub-signature-256": signature,
        },
    )


def _wait_for_states(client, expected, *, check=None, ready=None):
    deadline = time.monotonic() + 30
    while True:
        response = client.get("/metrics.json")
        assert response.status_code == 200
        metrics = response.json()
        if check is not None:
            check(metrics)
        states = {issue["number"]: issue["state"] for issue in metrics["issues_detail"]}
        if all(states.get(number) == state for number, state in expected.items()) and (
            ready is None or ready(metrics)
        ):
            return metrics
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out waiting for states {expected}; got {states}")
        time.sleep(0.2)


def _assert_triage_comment_lists_cves(github, number):
    issue = github.issues[number]
    cve_ids = set(re.findall(r"CVE-\d{4}-\d+", issue.body))
    comments = github.comments.get(number, [])
    assert cve_ids
    assert any(cve_ids <= set(re.findall(r"CVE-\d{4}-\d+", comment)) for comment in comments)


def test_demo_end_to_end(tmp_path):
    settings = _settings(tmp_path / "demo.db")
    app = create_app(settings)

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        github = app.state.deps.github
        devin = app.state.deps.devin

        assert all(label.name in github.existing_labels for label in MANAGED_LABELS)
        _wait_for_states(
            client,
            {
                2: IssueState.NOT_REACHABLE.value,
                3: IssueState.PR_OPENED.value,
                4: IssueState.NEEDS_HUMAN.value,
            },
        )

        assert github.closed[2] == "not_planned"
        assert LABEL_LOW_PRIORITY in github.labels(2)
        assert app.state.deps.db.get_issue(3).pr_url == ("https://demo.invalid/dmonroym0/superset/pull/103")
        assert LABEL_PR_OPENED in github.labels(3)
        assert LABEL_NEEDS_HUMAN in github.labels(4)
        assert "major version bump" in app.state.deps.db.get_issue(4).route_reason.lower()
        for number in (2, 3, 4):
            _assert_triage_comment_lists_cves(github, number)

        payload = build_payload(1)
        accepted = _post_webhook(client, 1, "e2e-issue-1", body=payload)
        duplicate = _post_webhook(client, 1, "e2e-issue-1", body=payload)
        assert accepted.status_code == 202
        assert accepted.json()["status"] == "accepted"
        assert duplicate.json()["status"] == "duplicate"

        bad_signature = _post_webhook(client, 5, "e2e-issue-5-bad-signature", bad_signature=True)
        assert bad_signature.status_code == 401
        other_repo = _post_webhook(client, 1, "e2e-other-repo", repo="dmonroym0/devin-superset-demo")
        assert other_repo.json()["status"] == "ignored"
        accepted_five = _post_webhook(client, 5, "e2e-issue-5")
        assert accepted_five.status_code == 202
        metrics = _wait_for_states(
            client,
            {
                1: IssueState.PR_OPENED.value,
                2: IssueState.NOT_REACHABLE.value,
                3: IssueState.PR_OPENED.value,
                4: IssueState.NEEDS_HUMAN.value,
                5: IssueState.NEEDS_HUMAN.value,
            },
        )

        assert client.get("/").status_code == 200
        assert "org default" in client.get("/").text
        assert client.post("/sweep").status_code == 200
        assert metrics["issues"]["pr_opened"] == 2
        assert metrics["issues"]["needs_human"] == 2
        assert metrics["issues"]["not_reachable"] == 1
        assert metrics["automation_rate"] == 0.6
        assert metrics["acu"]["committed"] == 5 * 5 + 15 * 2
        assert metrics["acu"]["committed"] <= 120
        assert all(session["devin_mode"] is None for session in metrics["sessions"])
        assert all(session["acus_consumed"] == 0.0 for session in metrics["sessions"])
        triage_ids = {
            session["session_id"] for session in metrics["sessions"] if session["stage"] == "triage"
        }
        fix_ids = {session["session_id"] for session in metrics["sessions"] if session["stage"] == "fix"}
        assert triage_ids <= set(devin.archived)
        assert not fix_ids & set(devin.archived)
        assert len(triage_ids) == 5
        assert len(fix_ids) == 2
        assert all(
            request.max_acu_limit == (5 if "stage-triage" in request.tags else 15)
            for request in devin.requests
        )
        assert all(request.to_payload()["repos"] == ["dmonroym0/superset"] for request in devin.requests)

        synthetic_body = (
            "Package: demo-package (pip)\n"
            "Current -> fixed: 1.0.0 -> 1.0.1 (patch)\n"
            "CVEs: CVE-2025-0001\n"
            "Risk notes: a test-only dependency update with a reproducible security finding.\n"
        )
        github.add_issue(
            Issue(
                number=9001,
                title="[Security] Upgrade demo-package 1.0.0 -> 1.0.1",
                body=synthetic_body,
                labels=("devin:fixplease",),
            )
        )
        synthetic_payload = json.loads(build_payload(9001))
        synthetic_payload["issue"]["body"] = synthetic_body
        synthetic_payload["issue"]["labels"] = [{"name": "devin:fixplease"}]
        synthetic_body_bytes = json.dumps(synthetic_payload, separators=(",", ":")).encode()
        synthetic_response = _post_webhook(client, 9001, "e2e-issue-9001", body=synthetic_body_bytes)
        assert synthetic_response.status_code == 202
        _wait_for_states(client, {9001: IssueState.NEEDS_HUMAN.value})
        assert LABEL_NEEDS_HUMAN in github.labels(9001)
        assert LABEL_TRIAGE_REJECTED in github.labels(9001)
        assert any(REJECTED_PR_URL in comment for comment in github.comments[9001])

    budget_app = create_app(_settings(tmp_path / "budget.db", ACU_CEILING="20"))
    with TestClient(budget_app, client=("127.0.0.1", 50000)) as client:
        budget_github = budget_app.state.deps.github

        def within_budget(metrics):
            assert metrics["acu"]["committed"] <= 20

        budget_metrics = _wait_for_states(
            client,
            {
                2: IssueState.NOT_REACHABLE.value,
                3: IssueState.TRIAGED.value,
                4: IssueState.NEEDS_HUMAN.value,
            },
            check=within_budget,
            ready=lambda _: LABEL_QUEUED_BUDGET in budget_github.labels(3),
        )
        assert budget_metrics["acu"]["committed"] == 15
        assert LABEL_QUEUED_BUDGET in budget_github.labels(3)
        assert budget_app.state.deps.db.get_issue(3).state is IssueState.TRIAGED
        assert not any("stage-fix" in request.tags for request in budget_app.state.deps.devin.requests)

    mode_app = create_app(_settings(tmp_path / "mode.db", DEVIN_MODE_TRIAGE="fast"))
    with TestClient(mode_app, client=("127.0.0.1", 50000)) as client:
        mode_metrics = _wait_for_states(
            client,
            {
                2: IssueState.NOT_REACHABLE.value,
                3: IssueState.PR_OPENED.value,
                4: IssueState.NEEDS_HUMAN.value,
            },
        )
        requests = mode_app.state.deps.devin.requests
        triage_requests = [request for request in requests if "stage-triage" in request.tags]
        fix_requests = [request for request in requests if "stage-fix" in request.tags]
        assert triage_requests and fix_requests
        assert all(request.devin_mode == "fast" for request in triage_requests)
        assert all(request.to_payload()["devin_mode"] == "fast" for request in triage_requests)
        assert all(request.devin_mode is None for request in fix_requests)
        assert all("devin_mode" not in request.to_payload() for request in fix_requests)
        assert all(
            session["devin_mode"] == "fast"
            for session in mode_metrics["sessions"]
            if session["stage"] == Stage.TRIAGE.value
        )
        assert all(
            session["devin_mode"] is None
            for session in mode_metrics["sessions"]
            if session["stage"] == Stage.FIX.value
        )
