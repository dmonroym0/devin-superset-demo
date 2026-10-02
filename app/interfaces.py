"""Client protocols and the dependency bundle passed to every module's register()."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from app.models import Issue, LabelSpec, Playbook, SessionInfo, SessionRequest

if TYPE_CHECKING:
    from app.budget import Budget
    from app.config import Settings
    from app.db import Database


class GitHubClient(Protocol):
    """All calls are scoped to settings.github_repo (dmonroym0/superset)."""

    async def list_open_issues_with_label(self, label: str) -> list[Issue]: ...
    async def get_issue(self, number: int) -> Issue: ...
    async def ensure_labels(self, labels: Sequence[LabelSpec]) -> list[str]:
        """Create missing labels; return the names created."""
        ...

    async def add_labels(self, number: int, labels: Sequence[str]) -> None: ...
    async def remove_label(self, number: int, label: str) -> None:
        """No-op if the label is not on the issue."""
        ...

    async def create_comment(self, number: int, body: str) -> str:
        """Return the comment html_url."""
        ...

    async def close_issue(self, number: int, reason: str = "not_planned") -> None: ...
    async def create_issue(self, title: str, body: str, labels: Sequence[str]) -> Issue: ...
    async def aclose(self) -> None: ...


class DevinClient(Protocol):
    """Devin API v3, scoped to settings.devin_org_id."""

    async def list_playbooks(self) -> list[Playbook]: ...
    async def get_playbook(self, playbook_id: str) -> Playbook: ...
    async def create_session(self, request: SessionRequest) -> SessionInfo: ...
    async def get_session(self, session_id: str) -> SessionInfo: ...
    async def send_message(self, session_id: str, message: str) -> None: ...
    async def archive_session(self, session_id: str) -> None: ...
    async def aclose(self) -> None: ...


Clock = Callable[[], float]
BackgroundTask = Callable[[], Awaitable[None]]


@dataclass
class ResolvedPlaybooks:
    triage: Playbook
    fix: Playbook


@dataclass
class Deps:
    settings: Settings
    db: Database
    budget: Budget
    github: GitHubClient
    devin: DevinClient
    clock: Clock
    playbooks: ResolvedPlaybooks | None = None
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    background: list[BackgroundTask] = field(default_factory=list)
    startup: list[BackgroundTask] = field(default_factory=list)
