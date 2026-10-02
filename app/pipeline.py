"""Issue pipeline worker owned by child B."""

from fastapi import FastAPI

from app.interfaces import Deps


def register(app: FastAPI, deps: Deps) -> None:
    """Routes are registered by child B."""
