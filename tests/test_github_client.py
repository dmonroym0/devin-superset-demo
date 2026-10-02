import httpx
import pytest
import respx

from app.config import Settings
from app.github_client import GitHubError, HttpGitHubClient
from app.models import Issue, LabelSpec

BASE = "https://api.github.test"
REPO_PATH = "/repos/dmonroym0/superset"
FAKE_TOKEN = "ghp_test_token_value"


def _settings():
    return Settings.from_env(
        {
            "APP_MODE": "live",
            "GITHUB_API_BASE": BASE,
            "GITHUB_TOKEN": FAKE_TOKEN,
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
