"""GitHub client factory owned by child A."""

from app.config import Settings
from app.interfaces import GitHubClient


def build_github_client(settings: Settings) -> GitHubClient:
    raise NotImplementedError("built by child A")
