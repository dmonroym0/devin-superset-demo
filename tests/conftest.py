import pytest

from app.config import Settings
from app.models import Issue, LabelSpec, Playbook, SessionInfo, SessionRequest


class FakeGitHub:
    def __init__(self):
        self.ensure_labels_calls = []
        self.closed = False

    async def list_open_issues_with_label(self, label: str) -> list[Issue]:
        return []

    async def get_issue(self, number: int) -> Issue:
        raise NotImplementedError

    async def ensure_labels(self, labels: tuple[LabelSpec, ...] | list[LabelSpec]) -> list[str]:
        self.ensure_labels_calls.append(tuple(labels))
        return []

    async def add_labels(self, number: int, labels: list[str]) -> None:
        return None

    async def remove_label(self, number: int, label: str) -> None:
        return None

    async def create_comment(self, number: int, body: str) -> str:
        return ""

    async def close_issue(self, number: int, reason: str = "not_planned") -> None:
        return None

    async def create_issue(self, title: str, body: str, labels: list[str]) -> Issue:
        raise NotImplementedError

    async def aclose(self) -> None:
        self.closed = True


class FakeDevin:
    def __init__(self):
        self.closed = False

    async def list_playbooks(self) -> list[Playbook]:
        return []

    async def get_playbook(self, playbook_id: str) -> Playbook:
        raise NotImplementedError

    async def create_session(self, request: SessionRequest) -> SessionInfo:
        raise NotImplementedError

    async def get_session(self, session_id: str) -> SessionInfo:
        raise NotImplementedError

    async def send_message(self, session_id: str, message: str) -> None:
        return None

    async def archive_session(self, session_id: str) -> None:
        return None

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def fake_github():
    return FakeGitHub()


@pytest.fixture
def fake_devin():
    return FakeDevin()


@pytest.fixture
def test_settings(tmp_path):
    return Settings.from_env({"DB_PATH": str(tmp_path / "test.db")})
