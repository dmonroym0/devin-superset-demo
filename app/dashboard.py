"""Server-rendered operations dashboard."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app import i18n, metrics
from app.interfaces import Deps
from app.issue_actions import is_fork_pr_url
from app.models import FORK_REPO, Mode

_HERE = Path(__file__).parent
_STAGES = ("seen", "triage", "route", "fix", "pr")
_NAV = ("overview", "pipeline", "sessions", "cost")
_NAV_PATHS = {"overview": "/", "pipeline": "/pipeline", "sessions": "/sessions", "cost": "/cost"}
_STATE_TONES = {
    "seen": "accent",
    "queued_budget": "attention",
    "triaging": "accent",
    "triaged": "accent",
    "fixing": "accent",
    "pr_opened": "open",
    "needs_human": "attention",
    "not_reachable": "neutral",
    "error": "danger",
    "cancelled": "neutral",
}
_VERDICT_TONES = {"REACHABLE": "danger", "NOT_REACHABLE": "neutral", "UNKNOWN": "attention"}
_REASONS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), key)
    for pattern, key in (
        (r"^major version bump (?P<old>\S+) -> (?P<new>\S+)$", "major_bump"),
        (r"^reachable with medium or high confidence: (?P<ids>.+)$", "reachable"),
        (r"^all CVEs not reachable$", "all_not_reachable"),
        (r"^no CVE is reachable with medium or high confidence$", "none_qualifying"),
        (r"^could not confirm the bump is not major$", "bump_unknown"),
        (r"^no valid triage result$", "no_triage"),
        (r"^issue lists no CVE IDs; cannot validate triage output$", "no_cve_ids"),
        (r"^triage session opened a pull request \(read-only violated\)$", "triage_opened_pr"),
        (r"^issue closed$", "issue_closed"),
        (r"^trigger label removed$", "label_removed"),
        (r"^session creation outcome unknown; ACU reservation kept until reviewed$", "create_unknown"),
        (r"^fix session finished without a PR$", "fix_no_pr"),
        (r"^(?P<stage>triage|fix) session suspended \((?P<detail>.*)\)$", "suspended"),
        (r"^(?P<stage>triage|fix) session errored( \((?P<detail>.*)\))?$", "errored"),
        (r"^(?P<stage>triage|fix) session did not finish within (?P<limit>\d+)s$", "timed_out"),
        (r"^(?P<stage>triage|fix) session stopped: (?P<detail>.*)$", "stopped"),
        (r"^triage output unusable: (?P<detail>.*)$", "triage_unusable"),
    )
)
_SESSION_STATUSES = {"running", "blocked", "suspended", "error", "finished", "expired"}


def https_url(value: str | None) -> str | None:
    return value if isinstance(value, str) and value.startswith("https://") else None


def fork_pr_url(value: str | None) -> str | None:
    return value if is_fork_pr_url(value) else None


def pr_number(value: str | None) -> str | None:
    match = re.search(r"/pull/(\d+)$", value or "")
    return match.group(1) if match else None


def safe_next(value: str | None) -> str:
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    return value


def reason_text(t: i18n.Translator, reason: str | None) -> dict[str, str] | None:
    if not reason:
        return None
    for pattern, key in _REASONS:
        match = pattern.match(reason)
        if match:
            params = {name: value or "" for name, value in match.groupdict().items()}
            if "stage" in params:
                params["stage"] = t(f"stage.{params['stage']}").lower()
            return {"text": t(f"reason.{key}", **params), "lang": t.html_lang}
    return {"text": reason, "lang": "en"}


def _state_chip(t: i18n.Translator, state: str) -> dict[str, str]:
    return {"label": t(f"state.{state}"), "tone": _STATE_TONES.get(state, "neutral")}


def _session_status(t: i18n.Translator, session: dict[str, Any]) -> dict[str, str]:
    if session.get("finished"):
        return {"label": t("session_status.finished"), "tone": "done"}
    status = session.get("status") or ""
    if status in _SESSION_STATUSES:
        tone = {"running": "accent", "blocked": "attention", "error": "danger"}.get(status, "neutral")
        return {"label": t(f"session_status.{status}"), "tone": tone}
    return {"label": status or "—", "tone": "neutral"}


def _acus_consumed(t: i18n.Translator, session: dict[str, Any]) -> dict[str, Any]:
    value = session.get("acus_consumed") or 0
    if value:
        return {"text": t.acus(value), "note": False}
    if session.get("finished"):
        return {"text": t("sessions.not_reported"), "note": True}
    return {"text": t("sessions.not_yet"), "note": True}


def _issue_view(t: i18n.Translator, issue: dict[str, Any], sessions: dict[str, dict]) -> dict[str, Any]:
    cves = issue.get("cves") or []
    reachable = sum(cve.get("verdict") == "REACHABLE" for cve in cves)
    track = []
    for stage in issue.get("stage_track") or []:
        track.append(
            {
                **stage,
                "label": t(f"stage.{stage['key']}"),
                "state_label": t(f"track.{stage['state']}"),
                "duration": t.duration(stage.get("duration_s"))
                if stage.get("duration_s") is not None
                else None,
            }
        )
    linked_sessions = {
        stage: sessions.get(session_id)
        for stage, session_id in (issue.get("session_ids") or {}).items()
        if session_id
    }
    route = issue.get("route") or {}
    return {
        **issue,
        "url": f"https://github.com/{FORK_REPO}/issues/{issue['number']}",
        "detail_url": f"/issues/{issue['number']}",
        "chip": _state_chip(t, issue["state"]),
        "track": track,
        "safe_pr_url": fork_pr_url(issue.get("pr_url")),
        "pr_number": pr_number(fork_pr_url(issue.get("pr_url"))),
        "reason": reason_text(t, route.get("reason") or issue.get("route_reason")),
        "route_action": t(f"route.{route['action']}") if route.get("action") else None,
        "verdict_summary": t("verdicts.summary", reachable=reachable, total=len(cves)) if cves else None,
        "cve_views": [
            {
                **cve,
                "verdict_label": t(f"verdict.{cve['verdict']}"),
                "tone": _VERDICT_TONES.get(cve["verdict"], "neutral"),
                "confidence_label": t(f"confidence.{cve['confidence']}")
                if cve.get("confidence")
                else t("confidence.missing"),
            }
            for cve in cves
        ],
        "sessions": {
            stage: {**session, "safe_url": https_url(session.get("url"))}
            for stage, session in linked_sessions.items()
            if session
        },
    }


def _event_sentence(t: i18n.Translator, event: Any, issue: dict[str, Any] | None) -> dict[str, str | None]:
    kind, detail = event.kind, event.detail
    stage = "triage" if kind.startswith("triage") else "fix" if kind.startswith("fix") else None
    params: dict[str, Any] = {"detail": detail}
    key = f"event.{kind}"
    if kind in {"triage_started", "fix_started", "nudged", "readopted_after_restart", "triaged", "fix_no_pr"}:
        params["session"] = detail
    if kind == "triaged" and issue and issue.get("cves"):
        cves = issue["cves"]
        reachable = sum(cve.get("verdict") == "REACHABLE" for cve in cves)
        key = "event.triaged_counts"
        params.update(reachable=reachable, total=len(cves))
    if kind == "routed":
        action, _, reason = detail.partition(": ")
        if action in {"fix", "needs_human", "close_low_priority"}:
            key = f"event.routed_{action}"
            params["reason"] = (reason_text(t, reason) or {"text": reason})["text"]
    if kind == "queued_budget":
        match = re.match(r"^(triage|fix) cap (\d+) refused$", detail)
        if match:
            params.update(stage=t(f"stage.{match.group(1)}").lower(), cap=match.group(2))
        else:
            key = "event.fallback"
    if kind in {"queued_budget_commented", "queued_budget_comment_failed"}:
        match = re.match(r"^committed (\d+) / ceiling (\d+)$", detail)
        params.update(committed=match.group(1), ceiling=match.group(2)) if match else None
        if not match:
            key = "event.fallback"
    if kind == "pr_opened":
        numbers = [number for url in detail.split(", ") if (number := pr_number(fork_pr_url(url)))]
        params["prs"] = ", ".join(f"#{number}" for number in numbers) or detail
    if kind in {"escalated", "triage_suspended", "triage_invalid"}:
        params["reason"] = (reason_text(t, detail) or {"text": detail})["text"]
    if stage:
        params["stage"] = t(f"stage.{stage}").lower()
    if not t.has(key):
        key = "event.fallback"
    params.setdefault("kind", kind)
    return {"text": t(key, **params), "kind": kind}


_CHART = {"width": 880, "height": 220, "left": 56, "right": 150, "top": 16, "bottom": 32}
_MIN_CHART_SPAN_S = 600


def _timeline(snapshot: dict[str, Any], now: float) -> dict[str, Any]:
    reservations = snapshot["acu"].get("reservations") or []
    active = [item for item in reservations if not item.get("cancelled")]
    ceiling = snapshot["acu"]["ceiling"] or 1
    times = [datetime.fromisoformat(item["created_at"]).timestamp() for item in active]
    span = (max(times) - min(times)) if times else 0
    timeline: dict[str, Any] = {
        "active": active,
        "cancelled": len(reservations) - len(active),
        "span_s": span,
        "chart": None,
    }
    if len(active) < 3 or span < _MIN_CHART_SPAN_S:
        return timeline
    c = _CHART
    plot_w = c["width"] - c["left"] - c["right"]
    plot_h = c["height"] - c["top"] - c["bottom"]
    start, end = min(times), max(max(times), now)
    peak = max(ceiling, sum(item["cap"] for item in active))

    def x(moment: float) -> float:
        return round(c["left"] + (moment - start) / (end - start) * plot_w, 1)

    def y(value: float) -> float:
        return round(c["top"] + plot_h - value / peak * plot_h, 1)

    total = 0
    path = [f"M{x(start)},{y(0)}"]
    points = []
    for moment, item in zip(times, active, strict=True):
        path.append(f"H{x(moment)}")
        total += item["cap"]
        path.append(f"V{y(total)}")
        points.append({"x": x(moment), "y": y(total), "total": total, **item})
    path.append(f"H{x(end)}")
    timeline["chart"] = {
        **c,
        "path": "".join(path),
        "points": points,
        "ceiling_y": y(ceiling),
        "zero_y": y(0),
        "mid_y": y(ceiling / 2),
        "mid_value": ceiling / 2,
        "end_x": x(end),
        "end_y": y(total),
        "total": total,
        "start": datetime.fromtimestamp(start, UTC).isoformat(),
        "end": datetime.fromtimestamp(end, UTC).isoformat(),
    }
    return timeline


def register(app: FastAPI, deps: Deps) -> None:
    templates = Environment(
        loader=FileSystemLoader(_HERE / "templates"),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    templates.filters["https_url"] = https_url
    templates.filters["fork_pr_url"] = fork_pr_url
    templates.filters["iso"] = lambda value: datetime.fromtimestamp(value, UTC).isoformat()
    app.mount("/static", StaticFiles(directory=_HERE / "static"), name="static")

    def translator(request: Request) -> i18n.Translator:
        return i18n.Translator(
            i18n.negotiate(request.cookies.get(i18n.COOKIE_NAME), request.headers.get("accept-language"))
        )

    def render(request: Request, name: str, view: str, build: Callable[[i18n.Translator, dict], dict]):
        t = translator(request)
        snapshot = metrics.compute(deps.db, deps.budget, deps.settings, now=deps.clock())
        sessions = {session["session_id"]: session for session in snapshot["sessions"]}
        issues = [_issue_view(t, issue, sessions) for issue in snapshot["issues_detail"]]
        ceiling = snapshot["acu"]["ceiling"]
        committed = snapshot["acu"]["committed"]
        context = {
            "t": t,
            "view": view,
            "nav": [(key, _NAV_PATHS[key]) for key in _NAV],
            "path": request.url.path,
            "next": quote(request.url.path, safe="/"),
            "metrics": snapshot,
            "issues": issues,
            "is_demo": snapshot["mode"] == Mode.DEMO.value,
            "fork_repo": FORK_REPO,
            "trigger_label": deps.settings.trigger_label,
            "acu_percent": max(0.0, min(100.0, committed / ceiling * 100)) if ceiling else 0.0,
            "client_strings": t.client_strings(),
        }
        context.update(build(t, context))
        headers = {"Content-Language": t.html_lang, "Vary": "Accept-Language, Cookie"}
        return HTMLResponse(templates.get_template(name).render(context), headers=headers)

    @app.get("/", response_class=HTMLResponse)
    async def overview(request: Request) -> HTMLResponse:
        def build(t: i18n.Translator, context: dict) -> dict:
            issues = context["issues"]
            return {
                "attention": [issue for issue in issues if issue["state"] in {"needs_human", "error"}],
                "title": t("overview.title"),
            }

        return render(request, "overview.html", "overview", build)

    @app.get("/pipeline", response_class=HTMLResponse)
    async def pipeline_view(request: Request) -> HTMLResponse:
        return render(request, "pipeline.html", "pipeline", lambda t, _: {"title": t("pipeline.title")})

    @app.get("/issues/{number}", response_class=HTMLResponse)
    async def issue_view(request: Request, number: int) -> HTMLResponse:
        events = list(reversed(deps.db.list_events(issue_number=number, limit=500)))

        def build(t: i18n.Translator, context: dict) -> dict:
            issue = next((item for item in context["issues"] if item["number"] == number), None)
            return {
                "issue": issue,
                "title": t("issue.title", number=number),
                "history": [
                    {"at": event.created_at, **_event_sentence(t, event, issue), "detail": event.detail}
                    for event in events
                ],
            }

        response = render(request, "issue.html", "pipeline", build)
        if deps.db.get_issue(number) is None:
            response.status_code = 404
        return response

    @app.get("/sessions", response_class=HTMLResponse)
    async def sessions_view(request: Request) -> HTMLResponse:
        def build(t: i18n.Translator, context: dict) -> dict:
            rows = [
                {
                    **session,
                    "safe_url": https_url(session.get("url")),
                    "status_view": _session_status(t, session),
                    "consumed": _acus_consumed(t, session),
                    "duration": t.duration(session.get("duration_s")),
                }
                for session in sorted(
                    context["metrics"]["sessions"],
                    key=lambda session: (session["created_at"] or "", session["session_id"]),
                    reverse=True,
                )
            ]
            return {"rows": rows, "title": t("sessions.title")}

        return render(request, "sessions.html", "sessions", build)

    @app.get("/cost", response_class=HTMLResponse)
    async def cost_view(request: Request) -> HTMLResponse:
        def build(t: i18n.Translator, context: dict) -> dict:
            snapshot = context["metrics"]
            ledger = _timeline(snapshot, deps.clock())
            by_stage: dict[int, dict[str, int]] = {}
            for item in ledger["active"]:
                by_stage.setdefault(item["issue_number"], {"triage": 0, "fix": 0})
                by_stage[item["issue_number"]][item["stage"]] = (
                    by_stage[item["issue_number"]].get(item["stage"], 0) + item["cap"]
                )
            titles = {issue["number"]: issue for issue in context["issues"]}
            per_issue = [
                {"number": number, "issue": titles.get(number), **stages, "total": sum(stages.values())}
                for number, stages in sorted(by_stage.items())
            ]
            scale = max([row["total"] for row in per_issue] + [1])
            sessions = snapshot["sessions"]
            finished = [session for session in sessions if session.get("finished")]
            return {
                "title": t("cost.title"),
                "ledger": ledger,
                "per_issue": per_issue,
                "scale": scale,
                "metered_reported": any(session.get("acus_consumed") for session in sessions),
                "finished_count": len(finished),
            }

        return render(request, "cost.html", "cost", build)

    @app.get("/lang/{code}")
    async def set_language(code: str, next: str | None = None) -> Response:
        response = RedirectResponse(safe_next(next), status_code=303)
        if code in i18n.LOCALES:
            response.set_cookie(
                i18n.COOKIE_NAME, code, max_age=365 * 24 * 3600, samesite="lax", httponly=True
            )
        return response

    @app.get("/manifest.webmanifest")
    async def manifest(request: Request) -> JSONResponse:
        t = translator(request)
        body = {
            "name": t("app.name_long"),
            "short_name": t("app.name"),
            "description": t("app.description"),
            "lang": t.html_lang,
            "id": "/",
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "background_color": "#ffffff",
            "theme_color": "#1f2328",
            "icons": [
                {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
                {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
                {
                    "src": "/static/icons/icon-512-maskable.png",
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "maskable",
                },
                {"src": "/static/icons/icon.svg", "sizes": "any", "type": "image/svg+xml"},
            ],
        }
        return JSONResponse(
            body, media_type="application/manifest+json", headers={"Vary": "Accept-Language, Cookie"}
        )
