import asyncio
import threading
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.budget import Budget
from app.config import Settings
from app.db import Database
from app.fake_devin import FakeDevin
from app.fake_github import FakeGitHub
from app.github_client import GitHubError
from app.interfaces import Deps
from app.main import create_app
from app.models import LABEL_NEEDS_HUMAN, CompareResult, Issue, MergeUpstreamResult
from app.upstream_sync import _pull_request_body, run_upstream_sync


def _deps(tmp_path, *, scenario="merge"):
    settings = Settings.from_env(
        {
            "APP_MODE": "demo",
            "DB_PATH": str(tmp_path / f"{scenario}.db"),
            "UPSTREAM_SYNC_ENABLED": "true",
            "DEMO_UPSTREAM_SCENARIO": scenario,
        }
    )
    db = Database(settings.db_path)
    db.init_schema()
    github = FakeGitHub.from_seed(upstream_scenario=scenario)
    deps = Deps(
        settings=settings,
        db=db,
        budget=Budget(db, settings.acu_ceiling),
        github=github,
        devin=FakeDevin.from_scenarios(),
        clock=lambda: 1_800_000_000,
    )
    return deps, github


def test_demo_upstream_scenario_has_eight_representative_commits(tmp_path):
    deps, github = _deps(tmp_path)
    try:
        commits = github._upstream["commits"]
        subjects = [commit["subject"] for commit in commits]

        assert len(commits) == 8
        assert any(subject.startswith("feat!:") for subject in subjects)
        assert any(subject.startswith("chore(deps): bump ") for subject in subjects)
        assert any(commit.get("is_merge") for commit in commits)
        assert "Improve the shared metadata query planner" in subjects
        assert any("<script>" in subject and "[links]" in subject for subject in subjects)
        assert {"requirements/base.txt", "requirements/development.txt"} <= set(github._upstream["files"])
    finally:
        deps.db.close()


@pytest.mark.asyncio
async def test_upstream_merge_creates_changelog_pr_and_advances_sha(tmp_path):
    deps, github = _deps(tmp_path)
    try:
        result = await run_upstream_sync(deps)

        assert result["outcome"] == "merged"
        assert result["pr_url"] == "https://github.com/dmonroym0/superset/pull/900"
        assert len(github.created_prs) == 1
        assert github.created_prs[0]["base_branch"] == "master"
        assert "# Fork changelog" in github.created_prs[0]["content"]
        assert "never pushed directly to `master`" in github.created_prs[0]["body"]
        assert deps.db.get_meta("changelog_through_sha:master") == "2" * 40
        assert deps.db.latest_upstream_sync()["pr_url"] == result["pr_url"]
    finally:
        deps.db.close()


@pytest.mark.asyncio
async def test_second_upstream_sync_is_none_without_creating_another_pr(tmp_path):
    deps, github = _deps(tmp_path)
    try:
        await run_upstream_sync(deps)
        result = await run_upstream_sync(deps)

        assert result["outcome"] == "none"
        assert len(github.created_prs) == 1
        assert deps.db.get_meta("changelog_through_sha:master") == "2" * 40
    finally:
        deps.db.close()


@pytest.mark.asyncio
async def test_conflict_creates_one_issue_while_existing_issue_is_open(tmp_path):
    deps, github = _deps(tmp_path, scenario="conflict")
    try:
        first = await run_upstream_sync(deps)
        second = await run_upstream_sync(deps)

        assert first["outcome"] == second["outcome"] == "conflict"
        assert first["issue_number"] == second["issue_number"]
        assert len(github.created_issues) == 1
        body = github.created_issues[0].body
        assert "apache/superset" in body
        assert "dmonroym0/superset" in body
        assert "git fetch upstream" in body
        assert "never auto-resolves" in body
        assert deps.db.get_meta("conflict_issue_number:master") == str(first["issue_number"])
        assert deps.db.latest_upstream_sync()["detail"] == f"existing issue #{first['issue_number']}"
    finally:
        deps.db.close()


@pytest.mark.asyncio
async def test_closed_conflict_issue_is_replaced(tmp_path):
    deps, github = _deps(tmp_path, scenario="conflict")
    try:
        first = await run_upstream_sync(deps)
        await github.close_issue(first["issue_number"])
        second = await run_upstream_sync(deps)

        assert second["outcome"] == "conflict"
        assert second["issue_number"] != first["issue_number"]
        assert len(github.created_issues) == 2
    finally:
        deps.db.close()


@pytest.mark.asyncio
async def test_conflict_reuses_open_matching_issue_from_github(tmp_path):
    deps, github = _deps(tmp_path, scenario="conflict")
    title = "Upstream sync conflict on master"
    github.add_issue(
        Issue(
            number=80,
            title=title,
            body="A pull request, not a conflict issue.",
            labels=(LABEL_NEEDS_HUMAN,),
            is_pull_request=True,
        )
    )
    github.add_issue(
        Issue(number=81, title=title, body="Existing conflict issue.", labels=(LABEL_NEEDS_HUMAN,))
    )
    try:
        result = await run_upstream_sync(deps)

        assert result == {"outcome": "conflict", "issue_number": 81}
        assert github.created_issues == []
        assert deps.db.get_meta("conflict_issue_number:master") == "81"
    finally:
        deps.db.close()


@pytest.mark.asyncio
async def test_changelog_pr_failure_does_not_advance_sha_and_retries(tmp_path):
    deps, github = _deps(tmp_path)
    original_create = github.create_changelog_pr
    calls = 0

    async def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise GitHubError(503, "POST", "/repos/dmonroym0/superset/pulls")
        return await original_create(*args, **kwargs)

    github.create_changelog_pr = fail_once
    try:
        first = await run_upstream_sync(deps)
        assert first["outcome"] == "error"
        assert deps.db.get_meta("changelog_through_sha:master") is None
        assert deps.db.latest_upstream_sync()["detail"] == "GitHub status 503"

        second = await run_upstream_sync(deps)
        assert second["outcome"] == "merged"
        assert deps.db.get_meta("changelog_through_sha:master") == "2" * 40
        assert len(github.created_prs) == 1
    finally:
        deps.db.close()


@pytest.mark.asyncio
async def test_repeated_changelog_pr_failures_keep_original_range_for_retry(tmp_path):
    deps, github = _deps(tmp_path)
    original_create = github.create_changelog_pr
    calls = 0

    async def fail_twice(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise GitHubError(503, "POST", "/repos/dmonroym0/superset/pulls")
        return await original_create(*args, **kwargs)

    github.create_changelog_pr = fail_twice
    try:
        first = await run_upstream_sync(deps)
        second = await run_upstream_sync(deps)
        assert first["outcome"] == "error"
        assert second["outcome"] == "error"
        assert deps.db.get_meta("changelog_through_sha:master") is None

        third = await run_upstream_sync(deps)
        assert third["outcome"] == "merged"
        assert third["pr_url"] == "https://github.com/dmonroym0/superset/pull/900"
        assert deps.db.get_meta("changelog_through_sha:master") == "2" * 40
        assert len(github.created_prs) == 1
    finally:
        deps.db.close()


def test_pull_request_body_flags_incomplete_dependency_file_list():
    body = _pull_request_body(
        (),
        ("requirements/base.txt",),
        "master",
        files_truncated=True,
    )

    assert "_Dependency list may be incomplete: GitHub compare returned its 300-file cap._" in body


@pytest.mark.asyncio
async def test_upstream_cursor_is_scoped_to_configured_branch(tmp_path):
    deps, github = _deps(tmp_path)
    stable_before = "3" * 40
    stable_after = "4" * 40
    sha_calls = 0
    comparisons = []

    async def stable_branch_sha(branch):
        nonlocal sha_calls
        assert branch == "stable"
        sha_calls += 1
        return stable_before if sha_calls == 1 else stable_after

    async def no_stable_merge(branch):
        assert branch == "stable"
        return MergeUpstreamResult("none", "Already up to date")

    async def stable_compare(base, head):
        comparisons.append((base, head))
        return CompareResult((), ())

    try:
        master_result = await run_upstream_sync(deps)
        assert master_result["outcome"] == "merged"
        assert deps.db.get_meta("changelog_through_sha:master") == "2" * 40

        deps.settings = replace(deps.settings, upstream_sync_branch="stable")
        github.get_branch_sha = stable_branch_sha
        github.merge_upstream = no_stable_merge
        github.compare = stable_compare

        stable_result = await run_upstream_sync(deps)

        assert stable_result["outcome"] == "none"
        assert comparisons == [(stable_before, stable_after)]
        assert deps.db.get_meta("changelog_through_sha:stable") == stable_after
        assert deps.db.get_meta("changelog_through_sha:master") == "2" * 40
    finally:
        deps.db.close()


@pytest.mark.asyncio
async def test_pending_changelog_state_is_scoped_to_branch(tmp_path):
    deps, github = _deps(tmp_path)
    original_create = github.create_changelog_pr
    stable_before = "3" * 40
    stable_after = "4" * 40
    sha_calls = 0
    comparisons = []

    async def fail_master_pr(*args, **kwargs):
        raise GitHubError(503, "POST", "/repos/dmonroym0/superset/pulls")

    async def stable_branch_sha(branch):
        nonlocal sha_calls
        assert branch == "stable"
        sha_calls += 1
        return stable_before if sha_calls == 1 else stable_after

    async def no_stable_merge(branch):
        assert branch == "stable"
        return MergeUpstreamResult("none", "Already up to date")

    async def stable_compare(base, head):
        comparisons.append((base, head))
        return CompareResult((), ())

    github.create_changelog_pr = fail_master_pr
    try:
        master_result = await run_upstream_sync(deps)
        assert master_result["outcome"] == "error"
        assert deps.db.get_meta("changelog_pending_base_sha:master") == "1" * 40
        assert deps.db.get_meta("changelog_pending_outcome:master") == "merged"

        deps.settings = replace(deps.settings, upstream_sync_branch="stable")
        github.get_branch_sha = stable_branch_sha
        github.merge_upstream = no_stable_merge
        github.compare = stable_compare
        github.create_changelog_pr = original_create

        stable_result = await run_upstream_sync(deps)

        assert stable_result["outcome"] == "none"
        assert comparisons == [(stable_before, stable_after)]
        assert deps.db.get_meta("changelog_pending_base_sha:master") == "1" * 40
        assert deps.db.get_meta("changelog_pending_outcome:master") == "merged"
    finally:
        deps.db.close()


@pytest.mark.asyncio
async def test_conflict_issue_number_is_scoped_to_branch(tmp_path):
    deps, _ = _deps(tmp_path, scenario="conflict")
    try:
        master_result = await run_upstream_sync(deps)
        assert deps.db.get_meta("conflict_issue_number:master") == str(master_result["issue_number"])

        deps.settings = replace(deps.settings, upstream_sync_branch="stable")
        stable_result = await run_upstream_sync(deps)

        assert stable_result["outcome"] == "conflict"
        assert stable_result["issue_number"] != master_result["issue_number"]
        assert deps.db.get_meta("conflict_issue_number:stable") == str(stable_result["issue_number"])
    finally:
        deps.db.close()


@pytest.mark.asyncio
async def test_blob_load_failure_does_not_open_pr_or_advance_cursor(tmp_path):
    deps, github = _deps(tmp_path)

    async def fail_blob_load(path, ref):
        del path, ref
        raise GitHubError(503, "GET", "/repos/dmonroym0/superset/git/blobs/large-file")

    github.get_file = fail_blob_load
    try:
        result = await run_upstream_sync(deps)

        assert result == {"outcome": "error", "error": "github_unavailable"}
        assert github.created_prs == []
        assert deps.db.get_meta("changelog_through_sha:master") is None
    finally:
        deps.db.close()


def test_sync_endpoint_returns_busy_when_background_sync_holds_lock(tmp_path):
    settings = Settings.from_env(
        {
            "APP_MODE": "demo",
            "DB_PATH": str(tmp_path / "busy.db"),
            "UPSTREAM_SYNC_ENABLED": "true",
            "UPSTREAM_SYNC_INTERVAL_S": "86400",
        }
    )
    github = FakeGitHub.from_seed()
    entered = threading.Event()
    release = asyncio.Event()
    event_loop = []
    merge_calls = 0
    original_merge = github.merge_upstream

    async def blocked_merge(branch):
        nonlocal merge_calls
        merge_calls += 1
        if merge_calls == 1:
            event_loop.append(asyncio.get_running_loop())
            entered.set()
            await release.wait()
        return await original_merge(branch)

    github.merge_upstream = blocked_merge
    app = create_app(settings, github=github, devin=FakeDevin.from_scenarios())

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        try:
            assert entered.wait(timeout=2)
            response = client.post("/sync-upstream")
            assert response.status_code == 409
            assert response.json() == {"outcome": "busy"}
            assert merge_calls == 1
        finally:
            if event_loop:
                event_loop[0].call_soon_threadsafe(release.set)


def test_merge_upstream_error_returns_502(tmp_path):
    settings = Settings.from_env(
        {
            "APP_MODE": "demo",
            "DB_PATH": str(tmp_path / "merge-error.db"),
            "UPSTREAM_SYNC_ENABLED": "true",
        }
    )
    github = FakeGitHub.from_seed()

    async def error_merge(branch):
        del branch
        return MergeUpstreamResult("error", "simulated 422")

    github.merge_upstream = error_merge
    app = create_app(settings, github=github, devin=FakeDevin.from_scenarios())
    app.state.deps.background[:] = [
        task for task in app.state.deps.background if task.__name__ != "upstream_sync_loop"
    ]

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        response = client.post("/sync-upstream")

    assert response.status_code == 502
    assert response.json() == {
        "outcome": "error",
        "error": "merge_upstream_failed",
    }


def test_disabled_sync_has_no_loop_and_endpoint_is_not_found(tmp_path):
    settings = Settings.from_env(
        {
            "APP_MODE": "demo",
            "DB_PATH": str(tmp_path / "disabled.db"),
            "UPSTREAM_SYNC_ENABLED": "false",
            "SWEEP_INTERVAL_S": "86400",
        }
    )
    github = FakeGitHub.from_seed()
    app = create_app(settings, github=github, devin=FakeDevin.from_scenarios())

    assert not any(task.__name__ == "upstream_sync_loop" for task in app.state.deps.background)
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        response = client.post("/sync-upstream")
        metrics = client.get("/metrics.json").json()["upstream_sync"]

    assert response.status_code == 404
    assert response.json() == {"error": "upstream sync disabled"}
    assert github._upstream_merged is False
    assert metrics == {
        "enabled": False,
        "last_outcome": None,
        "last_at": None,
        "changelog_pr_url": None,
        "conflict_issue_number": None,
        "changelog_through_sha": None,
    }


def test_sync_endpoint_rejects_forwarded_for(tmp_path):
    settings = Settings.from_env(
        {
            "APP_MODE": "demo",
            "DB_PATH": str(tmp_path / "forwarded.db"),
            "UPSTREAM_SYNC_ENABLED": "true",
            "SWEEP_INTERVAL_S": "86400",
        }
    )
    app = create_app(
        settings,
        github=FakeGitHub.from_seed(),
        devin=FakeDevin.from_scenarios(),
    )

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        response = client.post("/sync-upstream", headers={"X-Forwarded-For": "127.0.0.1"})

    assert response.status_code == 403
    assert response.json() == {"error": "forbidden"}


def test_sync_metrics_include_last_run_state(tmp_path):
    settings = Settings.from_env(
        {
            "APP_MODE": "demo",
            "DB_PATH": str(tmp_path / "metrics.db"),
            "UPSTREAM_SYNC_ENABLED": "false",
            "UPSTREAM_SYNC_BRANCH": "stable",
        }
    )
    app = create_app(
        settings,
        github=FakeGitHub.from_seed(),
        devin=FakeDevin.from_scenarios(),
    )

    with TestClient(app) as client:
        deps = app.state.deps
        deps.db.set_meta("changelog_through_sha:master", "b" * 40)
        deps.db.set_meta("changelog_through_sha:stable", "a" * 40)
        deps.db.set_meta("conflict_issue_number:master", "901")
        deps.db.set_meta("conflict_issue_number:stable", "902")
        deps.db.record_upstream_sync(1_800_000_000, "conflict", issue_number=901)
        upstream = client.get("/metrics.json").json()["upstream_sync"]

    assert upstream == {
        "enabled": False,
        "last_outcome": "conflict",
        "last_at": "2027-01-15T08:00:00+00:00",
        "changelog_pr_url": None,
        "conflict_issue_number": 902,
        "changelog_through_sha": "a" * 40,
    }
