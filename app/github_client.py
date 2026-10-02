"""GitHub API clients for DEMO and LIVE modes."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from urllib.parse import quote, urljoin, urlsplit

import httpx

from app.config import Settings
from app.interfaces import GitHubClient
from app.models import Issue, LabelSpec, Mode

logger = logging.getLogger(__name__)


class GitHubError(Exception):
    def __init__(self, status_code: int, method: str, path: str):
        self.status_code = status_code
        self.method = method
        parsed_path = urlsplit(path).path
        self.path = parsed_path or path.split("?", 1)[0]
        super().__init__(f"GitHub API error: {method} {self.path} -> {status_code}")


def build_github_client(settings: Settings) -> GitHubClient:
    if settings.mode is Mode.DEMO:
        # local import: app.fake_github imports GitHubError from this module
        from app.fake_github import FakeGitHub

        return FakeGitHub.from_seed()
    return HttpGitHubClient(settings)


class HttpGitHubClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._settings = settings
        self._base = settings.github_api_base.rstrip("/")
        self._repo_path = f"/repos/{settings.github_repo}"
        self._client = httpx.AsyncClient(
            base_url=settings.github_api_base,
            timeout=20,
            transport=transport,
            headers={
                "Authorization": f"Bearer {settings.github_token.get()}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "devin-superset-demo",
            },
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: object = None,
        params: dict[str, object] | None = None,
        ok_statuses: Sequence[int] = (),
    ) -> httpx.Response:
        safe_path = urlsplit(path).path or path.split("?", 1)[0]
        for attempt in range(2):
            try:
                response = await self._client.request(method, path, json=json, params=params)
            except httpx.HTTPError as err:
                logger.warning("GitHub request %s %s -> 0", method, safe_path)
                raise GitHubError(0, method, safe_path) from err
            logger.info("GitHub request %s %s -> %s", method, safe_path, response.status_code)
            if (
                method in {"GET", "PUT", "PATCH", "DELETE"}
                and response.status_code in {502, 503, 504}
                and attempt == 0
            ):
                continue
            if response.status_code >= 400 and response.status_code not in ok_statuses:
                raise GitHubError(response.status_code, method, safe_path)
            return response
        raise AssertionError("unreachable")

    def _next_url(self, response: httpx.Response, current_url: str) -> str | None:
        link = response.links.get("next")
        if not link or not link.get("url"):
            return None
        next_url = urljoin(current_url, link["url"])
        base = urlsplit(self._base)
        candidate = urlsplit(next_url)
        if (candidate.scheme, candidate.netloc) != (base.scheme, base.netloc):
            return None
        expected_prefix = f"{urlsplit(self._base).path.rstrip('/')}{self._repo_path}/"
        if not candidate.path.startswith(expected_prefix):
            return None
        return next_url

    async def _paginated(self, path: str, *, params: dict[str, object] | None = None):
        url = f"{self._base}{path}"
        first = True
        seen_urls: set[str] = set()
        while url:
            if url in seen_urls:
                break
            seen_urls.add(url)
            response = await self._request(
                "GET",
                url if not first else path,
                params=params if first else None,
            )
            first = False
            yield response
            url = self._next_url(response, str(response.request.url)) or ""

    async def list_open_issues_with_label(self, label: str) -> list[Issue]:
        issues: list[Issue] = []
        async for response in self._paginated(
            f"{self._repo_path}/issues",
            params={"labels": label, "state": "open", "per_page": 100},
        ):
            for item in response.json():
                if "pull_request" in item:
                    continue
                issues.append(self._to_issue(item))
        return issues

    async def get_issue(self, number: int) -> Issue:
        response = await self._request("GET", f"{self._repo_path}/issues/{number}")
        return self._to_issue(response.json())

    def _to_issue(self, data: dict) -> Issue:
        return Issue(
            number=data["number"],
            title=data.get("title", ""),
            body=data.get("body") or "",
            labels=tuple(label["name"] for label in data.get("labels", [])),
            state=data.get("state", "open"),
            html_url=data.get("html_url", ""),
            repo=self._settings.github_repo,
            is_pull_request="pull_request" in data,
        )

    async def ensure_labels(self, specs: Sequence[LabelSpec]) -> list[str]:
        existing: set[str] = set()
        async for response in self._paginated(
            f"{self._repo_path}/labels",
            params={"per_page": 100},
        ):
            existing.update(item["name"] for item in response.json())
        created: list[str] = []
        for spec in specs:
            if spec.name in existing:
                continue
            response = await self._request(
                "POST",
                f"{self._repo_path}/labels",
                json={"name": spec.name, "color": spec.color, "description": spec.description},
                ok_statuses=(422,),
            )
            if response.status_code == 422:
                try:
                    data = response.json()
                    errors = data.get("errors", []) if isinstance(data, dict) else []
                except (ValueError, AttributeError):
                    errors = []
                if isinstance(errors, list) and any(
                    isinstance(error, dict) and error.get("code") == "already_exists" for error in errors
                ):
                    existing.add(spec.name)
                    continue
                raise GitHubError(422, "POST", f"{self._repo_path}/labels")
            created.append(spec.name)
            existing.add(spec.name)
        return created

    async def add_labels(self, number: int, labels: Sequence[str]) -> None:
        await self._request(
            "POST",
            f"{self._repo_path}/issues/{number}/labels",
            json={"labels": list(labels)},
        )

    async def remove_label(self, number: int, label: str) -> None:
        path = f"{self._repo_path}/issues/{number}/labels/{quote(label, safe='')}"
        await self._request("DELETE", path, ok_statuses=(404,))

    async def create_comment(self, number: int, body: str) -> str:
        response = await self._request(
            "POST",
            f"{self._repo_path}/issues/{number}/comments",
            json={"body": body},
        )
        return response.json()["html_url"]

    async def close_issue(self, number: int, reason: str = "not_planned") -> None:
        await self._request(
            "PATCH",
            f"{self._repo_path}/issues/{number}",
            json={"state": "closed", "state_reason": reason},
        )

    async def create_issue(self, title: str, body: str, labels: Sequence[str]) -> Issue:
        response = await self._request(
            "POST",
            f"{self._repo_path}/issues",
            json={"title": title, "body": body, "labels": list(labels)},
        )
        return self._to_issue(response.json())

    async def aclose(self) -> None:
        await self._client.aclose()
