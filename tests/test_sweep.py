import pytest
from fastapi.testclient import TestClient

from app.config import ConfigError, Settings
from app.fake_github import FakeGitHub
from app.github_client import GitHubError
from app.main import create_app
from app.models import FORK_REPO, Issue, IssueState
from app.sweep import run_sweep


@pytest.mark.asyncio
async def test_run_sweep_inserts_trigger_issues_and_skips_pull_requests(tmp_path, fake_devin):
    github = FakeGitHub.from_seed()
    github.add_issue(
        Issue(99, "Demo PR", "", labels=("devin:fixplease",), repo=FORK_REPO, is_pull_request=True)
    )
    app = create_app(
        Settings.from_env({"DB_PATH": str(tmp_path / "sweep.db")}),
        github=github,
        devin=fake_devin,
        clock=lambda: 100.0,
    )
    deps = app.state.deps
    deps.db.init_schema()
    try:
        first = await run_sweep(deps)
        second = await run_sweep(deps)
        assert first == {"found": 3, "new": 3, "new_issues": [2, 3, 4]}
        assert second == {"found": 3, "new": 0, "new_issues": []}
        assert deps.db.get_issue(99) is None
    finally:
        deps.db.close()


@pytest.mark.asyncio
async def test_run_sweep_reports_github_errors(tmp_path, fake_devin):
    class FailingGitHub(FakeGitHub):
        async def list_open_issues_with_label(self, label):
            raise GitHubError(503, "GET", "/repos/dmonroym0/superset/issues")

    app = create_app(
        Settings.from_env({"DB_PATH": str(tmp_path / "failing.db")}),
        github=FailingGitHub.from_seed(),
        devin=fake_devin,
    )
    deps = app.state.deps
    deps.db.init_schema()
    try:
        assert await run_sweep(deps) == {"error": "github_unavailable"}
    finally:
        deps.db.close()


@pytest.mark.asyncio
async def test_single_issue_sweep_seeds_open_labeled_issue(tmp_path, fake_devin):
    github = FakeGitHub.from_seed()
    github.add_issue(Issue(44, "New labeled issue", "", labels=("devin:fixplease",)))
    app = create_app(
        Settings.from_env({"DB_PATH": str(tmp_path / "single.db")}),
        github=github,
        devin=fake_devin,
    )
    deps = app.state.deps
    deps.db.init_schema()

    result = await run_sweep(deps, 44)

    assert result == {"found": 1, "new": 1, "new_issues": [44]}
    assert deps.db.get_issue(44).state is IssueState.SEEN
    assert [event.detail for event in deps.db.list_events(44)] == ["seen via sweep"]
    assert deps.wake.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "labels", "is_pull_request"),
    [
        ("open", (), False),
        ("closed", ("devin:fixplease",), False),
        ("open", ("devin:fixplease",), True),
    ],
)
async def test_single_issue_sweep_ignores_unlabeled_closed_and_pull_requests(
    tmp_path,
    fake_devin,
    state,
    labels,
    is_pull_request,
):
    github = FakeGitHub.from_seed()
    github.add_issue(
        Issue(
            45,
            "Ignored issue",
            "",
            labels=labels,
            state=state,
            is_pull_request=is_pull_request,
        )
    )
    app = create_app(
        Settings.from_env({"DB_PATH": str(tmp_path / f"ignored-{state}-{is_pull_request}.db")}),
        github=github,
        devin=fake_devin,
    )
    deps = app.state.deps
    deps.db.init_schema()

    result = await run_sweep(deps, 45)

    assert result == {"found": 0, "new": 0, "new_issues": []}
    assert deps.db.get_issue(45) is None
    assert not deps.wake.is_set()


def test_single_issue_sweep_endpoint_maps_github_errors(tmp_path, fake_devin):
    settings = Settings.from_env(
        {"DB_PATH": str(tmp_path / "single-errors.db"), "UPSTREAM_SYNC_ENABLED": "false"}
    )
    missing_app = create_app(settings, github=FakeGitHub.from_seed(), devin=fake_devin)
    with TestClient(missing_app, client=("127.0.0.1", 50000)) as client:
        missing = client.post("/sweep?issue=999")
    assert missing.status_code == 404
    assert missing.json() == {"error": "issue_not_found"}

    class UnavailableGitHub(FakeGitHub):
        async def get_issue(self, number):
            raise GitHubError(503, "GET", f"/repos/dmonroym0/superset/issues/{number}")

    unavailable_app = create_app(
        Settings.from_env(
            {"DB_PATH": str(tmp_path / "single-unavailable.db"), "UPSTREAM_SYNC_ENABLED": "false"}
        ),
        github=UnavailableGitHub.from_seed(),
        devin=fake_devin,
    )
    with TestClient(unavailable_app, client=("127.0.0.1", 50000)) as client:
        unavailable = client.post("/sweep?issue=3")
    assert unavailable.status_code == 502
    assert unavailable.json() == {"error": "github_unavailable"}


def test_sweep_endpoint_rejects_invalid_issue_query(tmp_path, fake_devin):
    app = create_app(
        Settings.from_env({"DB_PATH": str(tmp_path / "invalid-issue.db"), "UPSTREAM_SYNC_ENABLED": "false"}),
        github=FakeGitHub.from_seed(),
        devin=fake_devin,
    )
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        for value in ("abc", "0", "-1"):
            response = client.post("/sweep", params={"issue": value})
            assert response.status_code == 400
            assert response.json() == {"error": "invalid issue"}


def test_sweep_endpoint_is_limited_to_allowed_client_ips(tmp_path, fake_devin):
    def make_app(name):
        return create_app(
            Settings.from_env({"DB_PATH": str(tmp_path / name)}),
            github=FakeGitHub.from_seed(),
            devin=fake_devin,
        )

    with TestClient(make_app("loopback.db"), client=("127.0.0.1", 50000)) as client:
        assert client.post("/sweep").status_code == 200
    with TestClient(make_app("external.db"), client=("203.0.113.5", 50000)) as client:
        assert client.post("/sweep").status_code == 403
    with TestClient(make_app("forwarded.db"), client=("127.0.0.1", 50000)) as client:
        assert (
            client.post(
                "/sweep?issue=abc",
                headers={"X-Forwarded-For": "127.0.0.1"},
            ).status_code
            == 403
        )


def test_sweep_cidr_configuration_rejects_invalid_or_empty_values():
    for bad_value in ("not-a-cidr", " , "):
        with pytest.raises(ConfigError) as error:
            Settings.from_env({"SWEEP_ALLOWED_CIDRS": bad_value})
        assert "SWEEP_ALLOWED_CIDRS invalid" in str(error.value)
        assert bad_value.strip() not in str(error.value)


def test_sweep_cidr_configuration_accepts_stripped_values():
    settings = Settings.from_env({"SWEEP_ALLOWED_CIDRS": " 127.0.0.1 , ::1/128 "})
    assert settings.sweep_allowed_cidrs == ("127.0.0.1", "::1/128")
