"""Scripted Devin for DEMO mode and tests, driven by app/demo/scenarios.json."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.devin_client import DevinError
from app.models import Playbook, PullRequestRef, SessionInfo, SessionRequest, Stage
from app.schema_check import load_local_triage_schema

SCENARIOS_PATH = Path(__file__).resolve().parent / "demo" / "scenarios.json"
TRIAGE_PLAYBOOK_ID = "playbook-demo-triage"
FIX_PLAYBOOK_ID = "playbook-demo-fix"
DEFAULT_TRIAGE_TITLE = "CVE Reachability Triage (Read-Only)"
DEFAULT_FIX_TITLE = "Dependency Security Fix (superset)"


@dataclass
class _FakeSession:
    session_id: str
    issue_number: int
    stage: Stage
    tags: tuple[str, ...]
    polls: int = 0


def _unknown_triage_output(number: int) -> dict[str, Any]:
    return {
        "issue_number": number,
        "comment_url": f"https://demo.invalid/dmonroym0/superset/issues/{number}#triage",
        "package": "",
        "installed_version": "",
        "head_sha": "demo0000000000000000000000000000000000000",
        "cves": [
            {
                "cve_id": "CVE-UNKNOWN",
                "verdict": "UNKNOWN",
                "evidence": [{"file": "-", "line": 0, "description": "no scripted scenario"}],
                "gating": {"feature_flags": [], "config_options": [], "required_permissions": []},
                "confidence": "low",
                "notes": "",
            }
        ],
        "caveats": ["DEMO: no scripted scenario for this issue."],
    }


class FakeDevin:
    def __init__(
        self,
        scenarios: dict[str, Any],
        *,
        triage_title: str = DEFAULT_TRIAGE_TITLE,
        fix_title: str = DEFAULT_FIX_TITLE,
    ):
        self._scenarios = scenarios
        self.playbooks = {
            TRIAGE_PLAYBOOK_ID: Playbook(
                TRIAGE_PLAYBOOK_ID, triage_title, "!cve_triage", load_local_triage_schema()
            ),
            FIX_PLAYBOOK_ID: Playbook(FIX_PLAYBOOK_ID, fix_title, "!dep_security_fix", None),
        }
        self.requests: list[SessionRequest] = []
        self.messages: list[tuple[str, str]] = []
        self.archived: list[str] = []
        self.closed = False
        self._sessions: dict[str, _FakeSession] = {}
        self._counters: dict[tuple[Stage, int], int] = {}

    @classmethod
    def from_scenarios(cls, path: Path = SCENARIOS_PATH, **kwargs: str) -> FakeDevin:
        return cls(json.loads(path.read_text()), **kwargs)

    def _scenario(self, number: int, stage: Stage) -> dict[str, Any] | None:
        for group in ("issues", "synthetic"):
            entry = self._scenarios.get(group, {}).get(str(number))
            if entry is not None:
                return entry.get(stage.value)
        return None

    async def list_playbooks(self) -> list[Playbook]:
        return list(self.playbooks.values())

    async def get_playbook(self, playbook_id: str) -> Playbook:
        try:
            return self.playbooks[playbook_id]
        except KeyError:
            raise DevinError(404, "GET", f"/playbooks/{playbook_id}") from None

    async def create_session(self, request: SessionRequest) -> SessionInfo:
        self.requests.append(request)
        number = next((int(tag[6:]) for tag in request.tags if tag.startswith("issue-")), None)
        stage = next((Stage(tag[6:]) for tag in request.tags if tag.startswith("stage-")), None)
        if number is None or stage is None:
            raise DevinError(422, "POST", "/sessions")
        k = self._counters.get((stage, number), 0) + 1
        self._counters[(stage, number)] = k
        session_id = f"demo-{stage.value}-{number}-{k}"
        self._sessions[session_id] = _FakeSession(session_id, number, stage, tuple(request.tags))
        return self._info(self._sessions[session_id], settled=False)

    def _info(self, session: _FakeSession, *, settled: bool) -> SessionInfo:
        pulls: tuple[PullRequestRef, ...] = ()
        output = None
        if settled:
            scenario = self._scenario(session.issue_number, session.stage)
            if scenario is not None:
                pulls = tuple(
                    PullRequestRef(pr["pr_url"], pr.get("pr_state"))
                    for pr in scenario.get("pull_requests", [])
                )
                output = scenario.get("structured_output")
            elif session.stage is Stage.TRIAGE:
                output = _unknown_triage_output(session.issue_number)
        return SessionInfo(
            session_id=session.session_id,
            status="running",
            url=f"https://demo.invalid/sessions/{session.session_id}",
            status_detail="finished" if settled else "working",
            pull_requests=pulls,
            acus_consumed=0.0,
            structured_output=output,
            tags=session.tags,
        )

    async def get_session(self, session_id: str) -> SessionInfo:
        session = self._sessions.get(session_id)
        if session is None:
            raise DevinError(404, "GET", f"/sessions/{session_id}")
        session.polls += 1
        scenario = self._scenario(session.issue_number, session.stage)
        polls = scenario.get("polls") if scenario is not None else 1
        settled = polls is not None and session.polls >= polls
        return self._info(session, settled=settled)

    async def send_message(self, session_id: str, message: str) -> None:
        self.messages.append((session_id, message))

    async def archive_session(self, session_id: str) -> None:
        self.archived.append(session_id)

    async def aclose(self) -> None:
        self.closed = True
