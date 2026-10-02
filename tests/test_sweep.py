import pytest
from fastapi.testclient import TestClient

from app.config import ConfigError, Settings
from app.fake_github import FakeGitHub
from app.github_client import GitHubError
from app.main import create_app
from app.models import FORK_REPO, Issue
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
        assert client.post("/sweep", headers={"X-Forwarded-For": "127.0.0.1"}).status_code == 403


def test_sweep_cidr_configuration_rejects_invalid_or_empty_values():
    for bad_value in ("not-a-cidr", " , "):
        with pytest.raises(ConfigError) as error:
            Settings.from_env({"SWEEP_ALLOWED_CIDRS": bad_value})
        assert "SWEEP_ALLOWED_CIDRS invalid" in str(error.value)
        assert bad_value.strip() not in str(error.value)


def test_sweep_cidr_configuration_accepts_stripped_values():
    settings = Settings.from_env({"SWEEP_ALLOWED_CIDRS": " 127.0.0.1 , ::1/128 "})
    assert settings.sweep_allowed_cidrs == ("127.0.0.1", "::1/128")
