"""Devin API client factory owned by child B."""

from app.config import Settings
from app.interfaces import DevinClient


def build_devin_client(settings: Settings) -> DevinClient:
    raise NotImplementedError("built by child B")
