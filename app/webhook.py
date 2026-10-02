"""GitHub webhook routes owned by child A."""

from fastapi import FastAPI

from app.interfaces import Deps


def register(app: FastAPI, deps: Deps) -> None:
    """Routes are registered by child A."""
