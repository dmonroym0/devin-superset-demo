"""Dashboard routes owned by child C."""

from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app import metrics
from app.interfaces import Deps
from app.issue_actions import is_fork_pr_url
from app.models import FORK_REPO


def register(app: FastAPI, deps: Deps) -> None:
    templates = Environment(
        loader=FileSystemLoader(Path(__file__).parent / "templates"),
        autoescape=select_autoescape(["html"]),
    )

    def https_url(value: str | None) -> str | None:
        return value if isinstance(value, str) and value.startswith("https://") else None

    def fork_pr_url(value: str | None) -> str | None:
        return value if is_fork_pr_url(value) else None

    def duration(seconds: float | None) -> str:
        if seconds is None:
            return "—"
        total_seconds = max(0, int(seconds))
        if total_seconds < 60:
            return f"{total_seconds}s"
        if total_seconds < 3600:
            minutes, remainder = divmod(total_seconds, 60)
            return f"{minutes}m {remainder:02d}s"
        hours, remainder = divmod(total_seconds, 3600)
        return f"{hours}h {remainder // 60:02d}m"

    def percentage(value: float | None) -> str:
        return "—" if value is None else f"{round(value * 100):d}%"

    templates.filters["https_url"] = https_url
    templates.filters["fork_pr_url"] = fork_pr_url
    templates.filters["duration"] = duration
    templates.filters["percentage"] = percentage
    template = templates.get_template("board.html")

    @app.get("/", response_class=HTMLResponse)
    async def board() -> HTMLResponse:
        snapshot = metrics.compute(deps.db, deps.budget, deps.settings, now=deps.clock())
        events = [
            {
                "created_at": datetime.fromtimestamp(event.created_at, UTC).isoformat(),
                "issue_number": event.issue_number,
                "kind": event.kind,
                "detail": event.detail,
            }
            for event in deps.db.list_events(limit=50)
        ]
        ceiling = snapshot["acu"]["ceiling"]
        committed = snapshot["acu"]["committed"]
        acu_percent = max(0, min(100, committed / ceiling * 100)) if ceiling else 0
        for issue in snapshot["issues_detail"]:
            issue["url"] = f"https://github.com/{FORK_REPO}/issues/{issue['number']}"
        return HTMLResponse(
            template.render(
                metrics=snapshot,
                events=events,
                acu_percent=acu_percent,
                fork_repo=FORK_REPO,
            )
        )
