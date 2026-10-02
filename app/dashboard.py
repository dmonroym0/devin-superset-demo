"""Dashboard routes owned by child C."""

from fastapi import FastAPI

from app.interfaces import Deps


def register(app: FastAPI, deps: Deps) -> None:
    """Routes are registered by child C."""
