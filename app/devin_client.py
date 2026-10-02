"""Devin API v3 client (LIVE) and factory.

Endpoints and fields, checked against the v3 reference:

- Create session ``POST /v3/organizations/{org_id}/sessions``
  https://docs.devin.ai/api-reference/v3/sessions/post-organizations-sessions
  Body ``SessionCreateRequest``: prompt (required), title, tags, playbook_id, max_acu_limit,
  structured_output_schema, structured_output_required, repos (list of strings), devin_mode
  (normal|fast|lite|ultra|fusion). Response ``SessionResponse``.
- Get session ``GET /v3/organizations/{org_id}/sessions/{devin_id}``
  https://docs.devin.ai/api-reference/v3/sessions/get-organizations-session
  ``SessionResponse``: session_id, url, status (new|claimed|running|exit|error|suspended|resuming),
  status_detail (working|waiting_for_user|waiting_for_approval|finished or a suspension reason),
  pull_requests[] {pr_url, pr_state}, acus_consumed (number), structured_output, tags,
  updated_at (integer epoch seconds).
- Send message ``POST /v3/organizations/{org_id}/sessions/{devin_id}/messages`` body ``{"message": str}``
  https://docs.devin.ai/api-reference/v3/sessions/post-organizations-sessions-messages
- Archive ``POST /v3/organizations/{org_id}/sessions/{devin_id}/archive`` (no body)
  https://docs.devin.ai/api-reference/v3/sessions/post-organizations-sessions-archive
- List playbooks ``GET /v3/organizations/{org_id}/playbooks`` query ``first`` (1-200, default 100) and
  ``after`` (cursor); response ``{items, has_next_page, end_cursor, total}``
  https://docs.devin.ai/api-reference/v3/playbooks/organizations-playbooks
- Get playbook ``GET /v3/organizations/{org_id}/playbooks/{playbook_id}``
  https://docs.devin.ai/api-reference/v3/playbooks/get-organizations-playbook
  ``PlaybookResponse``: playbook_id, title, body, macro, structured_output_schema, ...
- Create playbook ``POST /v3/organizations/{org_id}/playbooks`` body ``{title, body, macro,
  structured_output_schema}`` (admin bootstrap only)
  https://docs.devin.ai/api-reference/v3/playbooks/post-organizations-playbooks
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.config import Settings
from app.interfaces import DevinClient
from app.models import Mode, Playbook, PullRequestRef, SessionInfo, SessionRequest

RETRY_STATUSES = frozenset({429, 502, 503, 504})


class DevinError(Exception):
    """A failed Devin API call. The message never includes credentials or response bodies."""

    def __init__(self, status_code: int, method: str, path: str):
        self.status_code = status_code
        self.method = method
        self.path = path
        what = f"HTTP {status_code}" if status_code else "network error"
        super().__init__(f"Devin API {method} {path} failed: {what}")


def build_devin_client(settings: Settings) -> DevinClient:
    if settings.mode is Mode.DEMO:
        from app.fake_devin import FakeDevin

        return FakeDevin.from_scenarios(
            triage_title=settings.playbook_triage_title, fix_title=settings.playbook_fix_title
        )
    return HttpDevinClient(settings)


def playbook_from_payload(data: dict[str, Any]) -> Playbook:
    return Playbook(
        playbook_id=data["playbook_id"],
        title=data["title"],
        macro=data.get("macro"),
        structured_output_schema=data.get("structured_output_schema"),
    )


def session_from_payload(data: dict[str, Any]) -> SessionInfo:
    pulls = tuple(
        PullRequestRef(pr_url=pr["pr_url"], pr_state=pr.get("pr_state"))
        for pr in data.get("pull_requests") or ()
        if pr.get("pr_url")
    )
    updated = data.get("updated_at")
    return SessionInfo(
        session_id=data["session_id"],
        status=data["status"],
        url=data.get("url") or "",
        status_detail=data.get("status_detail"),
        pull_requests=pulls,
        acus_consumed=float(data.get("acus_consumed") or 0.0),
        structured_output=data.get("structured_output"),
        tags=tuple(data.get("tags") or ()),
        updated_at=float(updated) if updated is not None else None,
    )


class HttpDevinClient:
    def __init__(self, settings: Settings, *, retry_backoff_s: float = 1.0, page_size: int = 100):
        if not settings.devin_org_id:
            raise ValueError("DEVIN_ORG_ID is required in LIVE mode")
        self._org_path = f"/v3/organizations/{settings.devin_org_id}"
        self._backoff = retry_backoff_s
        self._page_size = page_size
        self._http = httpx.AsyncClient(
            base_url=settings.devin_api_base,
            timeout=30,
            headers={"Authorization": f"Bearer {settings.devin_api_key.get()}"},
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        retry_statuses: frozenset[int] = RETRY_STATUSES,
        retry_transport: bool = True,
    ) -> dict[str, Any]:
        url = f"{self._org_path}{path}"
        for attempt in range(2):
            try:
                response = await self._http.request(method, url, json=json, params=params)
            except httpx.TransportError:
                if retry_transport and attempt == 0:
                    await asyncio.sleep(self._backoff)
                    continue
                raise DevinError(0, method, url) from None
            if response.status_code in retry_statuses and attempt == 0:
                await asyncio.sleep(self._backoff)
                continue
            if response.status_code >= 400:
                raise DevinError(response.status_code, method, url)
            if not response.content:
                return {}
            return response.json()
        raise DevinError(0, method, url)  # pragma: no cover

    async def list_playbooks(self) -> list[Playbook]:
        playbooks: list[Playbook] = []
        after: str | None = None
        while True:
            params: dict[str, Any] = {"first": self._page_size}
            if after:
                params["after"] = after
            page = await self._request("GET", "/playbooks", params=params)
            playbooks.extend(playbook_from_payload(item) for item in page.get("items", []))
            after = page.get("end_cursor")
            if not page.get("has_next_page") or not after:
                return playbooks

    async def get_playbook(self, playbook_id: str) -> Playbook:
        return playbook_from_payload(await self._request("GET", f"/playbooks/{playbook_id}"))

    async def create_playbook(
        self, title: str, body: str, macro: str | None, structured_output_schema: dict[str, Any] | None
    ) -> Playbook:
        payload: dict[str, Any] = {"title": title, "body": body}
        if macro:
            payload["macro"] = macro
        if structured_output_schema is not None:
            payload["structured_output_schema"] = structured_output_schema
        return playbook_from_payload(await self._request("POST", "/playbooks", json=payload))

    async def create_session(self, request: SessionRequest) -> SessionInfo:
        return session_from_payload(
            await self._request(
                "POST",
                "/sessions",
                json=request.to_payload(),
                retry_statuses=frozenset({429}),
                retry_transport=False,
            )
        )

    async def get_session(self, session_id: str) -> SessionInfo:
        return session_from_payload(await self._request("GET", f"/sessions/{session_id}"))

    async def send_message(self, session_id: str, message: str) -> None:
        await self._request("POST", f"/sessions/{session_id}/messages", json={"message": message})

    async def archive_session(self, session_id: str) -> None:
        await self._request("POST", f"/sessions/{session_id}/archive")

    async def aclose(self) -> None:
        await self._http.aclose()
