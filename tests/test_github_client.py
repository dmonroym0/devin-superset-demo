import base64
import json
from datetime import date

import httpx
import pytest
import respx

from app import github_client
from app.changelog import render_section
from app.config import Settings
from app.github_client import GitHubError, HttpGitHubClient
from app.models import Issue, LabelSpec, MergeUpstreamResult, UpstreamCommit

BASE = "https://api.github.test"
REPO_PATH = "/repos/dmonroym0/superset"
FAKE_TOKEN = "ghp_test_token_value"


def _settings(token=FAKE_TOKEN):
    return Settings.from_env(
        {
            "APP_MODE": "live",
            "GITHUB_API_BASE": BASE,
            "GITHUB_TOKEN": token,
        }
    )


def _issue(number: int, *, pull_request: bool = False) -> dict:
    issue = {
        "number": number,
        "title": f"Issue {number}",
        "body": None,
        "labels": [{"name": "devin:fixplease"}],
        "state": "open",
        "html_url": f"https://github.test/issues/{number}",
    }
    if pull_request:
        issue["pull_request"] = {"url": f"https://github.test/pulls/{number}"}
    return issue


@pytest.mark.asyncio
@respx.mock
async def test_list_issues_paginates_skips_prs_and_sends_auth():
    first = respx.get(f"{BASE}{REPO_PATH}/issues?labels=devin%3Afixplease&state=open&per_page=100").mock(
        return_value=httpx.Response(
            200,
            json=[_issue(1), _issue(2, pull_request=True)],
            headers={"Link": f'<{BASE}{REPO_PATH}/issues?page=2>; rel="next"'},
        )
    )
    second = respx.get(f"{BASE}{REPO_PATH}/issues?page=2").mock(
        return_value=httpx.Response(200, json=[_issue(3)])
    )
    client = HttpGitHubClient(_settings())
    try:
        issues = await client.list_open_issues_with_label("devin:fixplease")
        assert [issue.number for issue in issues] == [1, 3]
        assert first.calls[0].request.headers["Authorization"] == f"Bearer {FAKE_TOKEN}"
        assert second.calls[0].request.headers["Authorization"] == f"Bearer {FAKE_TOKEN}"
        assert dict(first.calls[0].request.url.params) == {
            "labels": "devin:fixplease",
            "state": "open",
            "per_page": "100",
        }
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_ensure_labels_creates_missing_and_tolerates_already_exists():
    respx.get(f"{BASE}{REPO_PATH}/labels").mock(return_value=httpx.Response(200, json=[{"name": "exists"}]))
    post = respx.post(f"{BASE}{REPO_PATH}/labels").mock(
        side_effect=[
            httpx.Response(201, json={"name": "new-one"}),
            httpx.Response(422, json={"errors": [{"code": "already_exists"}]}),
        ]
    )
    client = HttpGitHubClient(_settings())
    try:
        created = await client.ensure_labels(
            [
                LabelSpec("exists", "111111", "present"),
                LabelSpec("new-one", "222222", "create"),
                LabelSpec("raced", "333333", "already created"),
            ]
        )
        assert created == ["new-one"]
        assert post.call_count == 2
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_ensure_labels_retries_transient_post_once():
    respx.get(f"{BASE}{REPO_PATH}/labels").mock(return_value=httpx.Response(200, json=[]))
    post = respx.post(f"{BASE}{REPO_PATH}/labels").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(201, json={"name": "new-label"}),
        ]
    )
    client = HttpGitHubClient(_settings())
    try:
        created = await client.ensure_labels([LabelSpec("new-label", "222222", "create")])
        assert created == ["new-label"]
        assert post.call_count == 2
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_remove_404_and_retry_transient_status_once():
    remove = respx.delete(f"{BASE}{REPO_PATH}/issues/4/labels/devin%3Afixplease").mock(
        return_value=httpx.Response(404)
    )
    retry = respx.get(f"{BASE}{REPO_PATH}/issues/4").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json=_issue(4))]
    )
    client = HttpGitHubClient(_settings())
    try:
        await client.remove_label(4, "devin:fixplease")
        issue = await client.get_issue(4)
        assert isinstance(issue, Issue)
        assert issue.number == 4
        assert remove.call_count == 1
        assert retry.call_count == 2
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_post_does_not_retry_transient_status():
    post = respx.post(f"{BASE}{REPO_PATH}/issues/4/comments").mock(return_value=httpx.Response(503))
    client = HttpGitHubClient(_settings())
    try:
        with pytest.raises(GitHubError) as error:
            await client.create_comment(4, "test comment")
        assert error.value.status_code == 503
        assert post.call_count == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_response_github_error_retains_message_without_changing_str():
    message = "Resource not accessible by integration: workflows permission is required."
    respx.get(f"{BASE}{REPO_PATH}/issues/10").mock(
        return_value=httpx.Response(403, json={"message": message})
    )
    client = HttpGitHubClient(_settings())
    try:
        with pytest.raises(GitHubError) as error:
            await client.get_issue(10)

        assert error.value.status_code == 403
        assert error.value.message == message
        assert str(error.value) == f"GitHub API error: GET {REPO_PATH}/issues/10 -> 403"
    finally:
        await client.aclose()


def test_redact_secrets_redacts_token_formats_and_literal():
    secrets = (
        "ghp_" + "a" * 20,
        "gho_" + "b" * 20,
        "ghu_" + "c" * 20,
        "ghs_" + "d" * 20,
        "ghr_" + "e" * 20,
        "github_pat_" + "f" * 20,
        "Bearer bearer-secret",
        "token " + "g" * 20,
        "configured secret literal",
    )
    text = " ".join(secrets)

    redacted = github_client.redact_secrets(text, extra=(secrets[-1],))

    assert redacted.count("[redacted]") == len(secrets)
    assert all(secret not in redacted for secret in secrets)
    assert github_client.redact_secrets("No credentials here.") == "No credentials here."


@pytest.mark.asyncio
@respx.mock
async def test_foreign_next_link_is_not_followed_and_error_hides_token():
    respx.get(f"{BASE}{REPO_PATH}/issues").mock(
        return_value=httpx.Response(
            200,
            json=[_issue(1)],
            headers={"Link": '<https://other.example/issues?page=2>; rel="next"'},
        )
    )
    foreign = respx.get("https://other.example/issues?page=2").mock(
        return_value=httpx.Response(200, json=[_issue(2)])
    )
    failure = respx.get(f"{BASE}{REPO_PATH}/issues/10").mock(
        return_value=httpx.Response(500, json={"message": FAKE_TOKEN})
    )
    client = HttpGitHubClient(_settings())
    try:
        issues = await client.list_open_issues_with_label("devin:fixplease")
        assert [issue.number for issue in issues] == [1]
        assert foreign.call_count == 0
        with pytest.raises(GitHubError) as error:
            await client.get_issue(10)
        assert FAKE_TOKEN not in str(error.value)
        assert error.value.path == f"{REPO_PATH}/issues/10"
        assert failure.call_count == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_get_branch_sha_reads_commit_sha():
    respx.get(f"{BASE}{REPO_PATH}/branches/master").mock(
        return_value=httpx.Response(200, json={"commit": {"sha": "a" * 40}})
    )
    client = HttpGitHubClient(_settings())
    try:
        assert await client.get_branch_sha("master") == "a" * 40
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_get_file_decodes_content_and_returns_none_for_missing_file():
    content = "# Fork changelog\n\n## Upstream sync — caf\u00e9\n"
    route = respx.get(f"{BASE}{REPO_PATH}/contents/FORK_CHANGELOG.md").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "content": base64.b64encode(content.encode()).decode(),
                    "encoding": "base64",
                    "sha": "file-sha",
                },
            ),
            httpx.Response(404, json={"message": "Not Found"}),
        ]
    )
    client = HttpGitHubClient(_settings())
    try:
        assert await client.get_file("FORK_CHANGELOG.md", "master") == content
        assert await client.get_file("FORK_CHANGELOG.md", "master") is None
        assert route.call_count == 2
        assert [call.request.url.params["ref"] for call in route.calls] == ["master", "master"]
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "payload", "outcome"),
    [
        (200, {"merge_type": "merge", "message": "merged"}, "merged"),
        (200, {"merge_type": "fast-forward", "message": "fast-forward"}, "fast-forward"),
        (200, {"merge_type": "none", "message": "already up to date"}, "none"),
        (409, {"message": "conflict"}, "conflict"),
        (422, {"message": "unprocessable"}, "error"),
    ],
)
@respx.mock
async def test_merge_upstream_maps_github_outcomes(status, payload, outcome):
    route = respx.post(f"{BASE}{REPO_PATH}/merge-upstream").mock(
        return_value=httpx.Response(status, json=payload)
    )
    client = HttpGitHubClient(_settings())
    try:
        result = await client.merge_upstream("master")
        assert isinstance(result, MergeUpstreamResult)
        assert result.outcome == outcome
        assert route.calls[0].request.content == b'{"branch":"master"}'
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_merge_upstream_redacts_configured_token_and_github_token():
    configured_token = "configured-token-1234567890"
    github_token = "ghp_" + "h" * 24
    respx.post(f"{BASE}{REPO_PATH}/merge-upstream").mock(
        return_value=httpx.Response(
            422,
            json={"message": f"Rejected {configured_token}; leaked {github_token}"},
        )
    )
    client = HttpGitHubClient(_settings(token=configured_token))
    try:
        result = await client.merge_upstream("master")

        assert result.outcome == "error"
        assert configured_token not in result.message
        assert github_token not in result.message
        assert result.message.count("[redacted]") == 2
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_merge_upstream_does_not_retry_transient_post():
    route = respx.post(f"{BASE}{REPO_PATH}/merge-upstream").mock(return_value=httpx.Response(503))
    client = HttpGitHubClient(_settings())
    try:
        with pytest.raises(GitHubError) as error:
            await client.merge_upstream("master")
        assert error.value.status_code == 503
        assert route.call_count == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_compare_returns_commit_subjects_merge_flags_and_files():
    base_sha = "a" * 40
    head_sha = "b" * 40
    respx.get(f"{BASE}{REPO_PATH}/compare/{base_sha}...{head_sha}").mock(
        return_value=httpx.Response(
            200,
            json={
                "commits": [
                    {
                        "sha": "c" * 40,
                        "commit": {"message": "feat: add thing\n\nbody"},
                        "parents": [{"sha": "p1"}],
                    },
                    {
                        "sha": "d" * 40,
                        "commit": {"message": "Merge pull request"},
                        "parents": [{"sha": "p1"}, {"sha": "p2"}],
                    },
                ],
                "files": [{"filename": "requirements/base.txt"}, {"filename": "app/main.py"}],
            },
        )
    )
    client = HttpGitHubClient(_settings())
    try:
        result = await client.compare(base_sha, head_sha)
        assert result.commits == (
            UpstreamCommit("c" * 40, "feat: add thing", False),
            UpstreamCommit("d" * 40, "Merge pull request", True),
        )
        assert result.files == ("requirements/base.txt", "app/main.py")
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_compare_paginates_all_commits_and_keeps_first_page_files():
    base_sha = "a" * 40
    head_sha = "b" * 40
    route = respx.get(f"{BASE}{REPO_PATH}/compare/{base_sha}...{head_sha}").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "total_commits": 270,
                    "commits": [
                        {
                            "sha": f"{index:040x}",
                            "commit": {"message": f"fix: item {index}"},
                            "parents": [{"sha": "parent"}],
                        }
                        for index in range(100)
                    ],
                    "files": [{"filename": "requirements/base.txt"}],
                },
            ),
            httpx.Response(
                200,
                json={
                    "total_commits": 270,
                    "commits": [
                        {
                            "sha": f"{index:040x}",
                            "commit": {"message": f"fix: item {index}"},
                            "parents": [{"sha": "parent"}],
                        }
                        for index in range(100, 200)
                    ],
                    "files": [{"filename": "ignored/page-two.txt"}],
                },
            ),
            httpx.Response(
                200,
                json={
                    "total_commits": 270,
                    "commits": [
                        {
                            "sha": f"{index:040x}",
                            "commit": {"message": f"fix: item {index}"},
                            "parents": [{"sha": "parent"}],
                        }
                        for index in range(200, 270)
                    ],
                    "files": [{"filename": "ignored/page-three.txt"}],
                },
            ),
        ]
    )
    client = HttpGitHubClient(_settings())
    try:
        result = await client.compare(base_sha, head_sha)

        assert len(result.commits) == 270
        assert result.files == ("requirements/base.txt",)
        assert [dict(call.request.url.params) for call in route.calls] == [
            {"per_page": "100", "page": "1"},
            {"per_page": "100", "page": "2"},
            {"per_page": "100", "page": "3"},
        ]
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_compare_file_cap_marks_rendered_dependency_list_incomplete():
    base_sha = "a" * 40
    head_sha = "b" * 40
    respx.get(f"{BASE}{REPO_PATH}/compare/{base_sha}...{head_sha}").mock(
        return_value=httpx.Response(
            200,
            json={
                "total_commits": 0,
                "commits": [],
                "files": [{"filename": f"requirements/file-{index}.txt"} for index in range(300)],
            },
        )
    )
    client = HttpGitHubClient(_settings())
    try:
        result = await client.compare(base_sha, head_sha)
        section = render_section(
            result.commits,
            result.files,
            base_sha,
            head_sha,
            date(2026, 10, 2),
            files_truncated=result.files_truncated,
        )

        assert result.files_truncated is True
        assert "_Dependency list may be incomplete: GitHub compare returned its 300-file cap._" in section
        assert section.index("### Dependency changes") < section.index("_Dependency list may be incomplete:")
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_get_file_fetches_git_blob_for_non_base64_contents():
    content = "# Fork changelog\n\n" + "large payload " * 100
    respx.get(f"{BASE}{REPO_PATH}/contents/FORK_CHANGELOG.md").mock(
        return_value=httpx.Response(
            200,
            json={"encoding": "none", "content": "", "sha": "blob-sha"},
        )
    )
    blob = respx.get(f"{BASE}{REPO_PATH}/git/blobs/blob-sha").mock(
        return_value=httpx.Response(
            200,
            json={
                "encoding": "base64",
                "content": base64.b64encode(content.encode()).decode(),
                "sha": "blob-sha",
            },
        )
    )
    client = HttpGitHubClient(_settings())
    try:
        assert await client.get_file("FORK_CHANGELOG.md", "devin/fork-changelog") == content
        assert blob.call_count == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "blob_response",
    [
        httpx.Response(503),
        httpx.Response(200, json={"encoding": "utf-8", "content": "not base64"}),
    ],
    ids=["request-failure", "unsupported-encoding"],
)
@respx.mock
async def test_get_file_rejects_failed_or_unsupported_blob_response(blob_response):
    respx.get(f"{BASE}{REPO_PATH}/contents/FORK_CHANGELOG.md").mock(
        return_value=httpx.Response(
            200,
            json={"encoding": "none", "content": "", "sha": "blob-sha"},
        )
    )
    respx.get(f"{BASE}{REPO_PATH}/git/blobs/blob-sha").mock(return_value=blob_response)
    client = HttpGitHubClient(_settings())
    try:
        with pytest.raises(GitHubError):
            await client.get_file("FORK_CHANGELOG.md", "devin/fork-changelog")
    finally:
        await client.aclose()


def _mock_changelog_pr_routes(
    *,
    ref_status=201,
    pull_status=201,
    content_status=404,
    existing_content=None,
    ref_message="Reference already exists",
    pull_message=None,
):
    ref = respx.post(f"{BASE}{REPO_PATH}/git/refs").mock(
        return_value=httpx.Response(ref_status, json={"message": ref_message})
    )
    content_payload = {"sha": "old-file-sha"}
    if existing_content is not None:
        content_payload.update(
            content=base64.b64encode(existing_content.encode()).decode(),
            encoding="base64",
        )
    content_get = respx.get(f"{BASE}{REPO_PATH}/contents/FORK_CHANGELOG.md").mock(
        return_value=httpx.Response(content_status, json=content_payload)
    )
    content_put = respx.put(f"{BASE}{REPO_PATH}/contents/FORK_CHANGELOG.md").mock(
        return_value=httpx.Response(201, json={"content": {"sha": "new-file-sha"}})
    )
    if pull_status == 201:
        pull_payload = {"html_url": "https://github.com/dmonroym0/superset/pull/901"}
    elif pull_message is not None:
        pull_payload = {"message": pull_message}
    else:
        pull_payload = {
            "message": "Validation Failed",
            "errors": [{"message": "A pull request already exists"}],
        }
    pulls_post = respx.post(f"{BASE}{REPO_PATH}/pulls").mock(
        return_value=httpx.Response(pull_status, json=pull_payload)
    )
    pulls_get = respx.get(f"{BASE}{REPO_PATH}/pulls").mock(
        return_value=httpx.Response(
            200,
            json=[{"html_url": "https://github.com/dmonroym0/superset/pull/900"}],
        )
    )
    return ref, content_get, content_put, pulls_post, pulls_get


@pytest.mark.asyncio
@respx.mock
async def test_create_changelog_pr_writes_only_new_branch_and_returns_url():
    branch = "devin/fork-changelog-abcdef123456"
    ref, content_get, content_put, pulls_post, _ = _mock_changelog_pr_routes()
    client = HttpGitHubClient(_settings())
    content = "# Fork changelog\n\n## Upstream sync"
    try:
        url = await client.create_changelog_pr(
            "master",
            "a" * 40,
            branch,
            content,
            "docs(fork-changelog): update",
            "Summary",
        )

        assert url == "https://github.com/dmonroym0/superset/pull/901"
        assert ref.call_count == 1
        ref_body = json.loads(ref.calls[0].request.content)
        assert ref_body["ref"] == "refs/heads/devin/fork-changelog-abcdef123456"
        assert ref_body["sha"] == "a" * 40
        assert content_get.calls[0].request.url.params["ref"] == branch
        put_payload = content_put.calls[0].request.content
        body = json.loads(put_payload)
        assert body["branch"] == branch
        assert base64.b64decode(body["content"]).decode() == content
        assert "sha" not in body
        assert pulls_post.calls[0].request.url.path == f"{REPO_PATH}/pulls"
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_create_changelog_pr_continues_when_branch_ref_exists():
    branch = "devin/fork-changelog-abcdef123456"
    ref, _, content_put, pulls_post, _ = _mock_changelog_pr_routes(ref_status=422)
    client = HttpGitHubClient(_settings())
    try:
        await client.create_changelog_pr(
            "master",
            "a" * 40,
            branch,
            "# Fork changelog\n",
            "title",
            "body",
        )
        assert ref.call_count == 1
        assert content_put.call_count == 1
        assert pulls_post.call_count == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_create_changelog_pr_propagates_ref_422_message():
    branch = "devin/fork-changelog-abcdef123456"
    message = "Validation failed"
    _mock_changelog_pr_routes(ref_status=422, ref_message=message)
    client = HttpGitHubClient(_settings())
    try:
        with pytest.raises(GitHubError) as error:
            await client.create_changelog_pr(
                "master",
                "a" * 40,
                branch,
                "# Fork changelog\n",
                "title",
                "body",
            )

        assert message in error.value.message
        assert str(error.value) == f"GitHub API error: POST {REPO_PATH}/git/refs -> 422"
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_create_changelog_pr_propagates_pull_422_message():
    branch = "devin/fork-changelog-abcdef123456"
    message = "No commits between master and x"
    _mock_changelog_pr_routes(pull_status=422, pull_message=message)
    client = HttpGitHubClient(_settings())
    try:
        with pytest.raises(GitHubError) as error:
            await client.create_changelog_pr(
                "master",
                "a" * 40,
                branch,
                "# Fork changelog\n",
                "title",
                "body",
            )

        assert message in error.value.message
        assert str(error.value) == f"GitHub API error: POST {REPO_PATH}/pulls -> 422"
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_create_changelog_pr_updates_existing_file_with_contents_sha():
    branch = "devin/fork-changelog-abcdef123456"
    previous_content = "# Fork changelog\n\n## Previous sync\n"
    _, content_get, content_put, _, _ = _mock_changelog_pr_routes(
        content_status=200,
        existing_content=previous_content,
    )
    client = HttpGitHubClient(_settings())
    try:
        await client.create_changelog_pr(
            "master",
            "a" * 40,
            branch,
            previous_content + "\n## New sync\n",
            "docs(fork-changelog): update",
            "Summary",
        )
        payload = json.loads(content_put.calls[0].request.content)
        assert content_get.calls[0].request.url.params["ref"] == branch
        assert payload["branch"] == branch
        assert payload["sha"] == "old-file-sha"
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_create_changelog_pr_returns_existing_pr_after_duplicate_422():
    branch = "devin/fork-changelog-abcdef123456"
    _, _, _, pulls_post, pulls_get = _mock_changelog_pr_routes(pull_status=422)
    client = HttpGitHubClient(_settings())
    try:
        url = await client.create_changelog_pr(
            "master",
            "a" * 40,
            branch,
            "# Fork changelog\n",
            "title",
            "body",
        )
        assert url == "https://github.com/dmonroym0/superset/pull/900"
        assert pulls_post.call_count == 1
        assert pulls_get.call_count == 1
        assert pulls_get.calls[0].request.url.params["head"] == (
            "dmonroym0:devin/fork-changelog-abcdef123456"
        )
        assert pulls_get.calls[0].request.url.params["state"] == "open"
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_create_changelog_pr_retry_skips_put_when_branch_content_matches():
    branch = "devin/fork-changelog-abcdef123456"
    content = "# Fork changelog\n\n## New sync\n"
    ref = respx.post(f"{BASE}{REPO_PATH}/git/refs").mock(
        side_effect=[
            httpx.Response(201, json={"ref": f"refs/heads/{branch}"}),
            httpx.Response(422, json={"message": "Reference already exists"}),
        ]
    )
    content_get = respx.get(f"{BASE}{REPO_PATH}/contents/FORK_CHANGELOG.md").mock(
        side_effect=[
            httpx.Response(404, json={"message": "Not Found"}),
            httpx.Response(
                200,
                json={
                    "content": base64.b64encode(content.encode()).decode(),
                    "encoding": "base64",
                    "sha": "file-sha",
                },
            ),
        ]
    )
    content_put = respx.put(f"{BASE}{REPO_PATH}/contents/FORK_CHANGELOG.md").mock(
        return_value=httpx.Response(200, json={"content": {"sha": "file-sha"}})
    )
    pulls = respx.post(f"{BASE}{REPO_PATH}/pulls").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(
                201,
                json={"html_url": "https://github.com/dmonroym0/superset/pull/902"},
            ),
        ]
    )
    client = HttpGitHubClient(_settings())
    try:
        kwargs = {
            "base_branch": "master",
            "head_sha": "a" * 40,
            "branch_name": branch,
            "content": content,
            "title": "docs(fork-changelog): retry",
            "body": "Summary",
        }
        with pytest.raises(GitHubError) as error:
            await client.create_changelog_pr(**kwargs)
        assert error.value.status_code == 503

        url = await client.create_changelog_pr(**kwargs)

        assert url == "https://github.com/dmonroym0/superset/pull/902"
        assert ref.call_count == 2
        assert content_get.call_count == 2
        assert content_put.call_count == 1
        assert pulls.call_count == 2
    finally:
        await client.aclose()
