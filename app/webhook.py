"""Authenticated GitHub webhook intake."""

from __future__ import annotations

import hashlib
import hmac
import json

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.interfaces import Deps
from app.models import FORK_REPO, Issue


def register(app: FastAPI, deps: Deps) -> None:
    @app.post("/webhooks/github")
    async def github_webhook(request: Request) -> JSONResponse:
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > 1_048_576:
                    return JSONResponse({"error": "payload too large"}, status_code=413)
            except ValueError:
                pass
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 1_048_576:
                return JSONResponse({"error": "payload too large"}, status_code=413)
            body.extend(chunk)
        raw = bytes(body)

        secret = deps.settings.github_webhook_secret
        if not secret:
            return JSONResponse({"error": "webhook disabled"}, status_code=503)

        signature = request.headers.get("x-hub-signature-256", "")
        expected = "sha256=" + hmac.new(secret.get().encode(), raw, hashlib.sha256).hexdigest()
        if not signature or not hmac.compare_digest(signature, expected):
            return JSONResponse({"error": "invalid signature"}, status_code=401)

        delivery_id = request.headers.get("x-github-delivery")
        if not delivery_id:
            return JSONResponse({"error": "missing delivery id"}, status_code=400)
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JSONResponse({"error": "invalid json"}, status_code=400)
        if not isinstance(payload, dict):
            return JSONResponse({"error": "invalid json"}, status_code=400)

        event = request.headers.get("x-github-event", "")
        if event == "ping":
            return JSONResponse({"status": "pong"})

        action = payload.get("action")
        action = action if isinstance(action, str) else ""
        issue_data = payload.get("issue")
        issue_number = (
            issue_data.get("number")
            if isinstance(issue_data, dict)
            and isinstance(issue_data.get("number"), int)
            and not isinstance(issue_data.get("number"), bool)
            else None
        )
        if not deps.db.record_delivery(
            delivery_id,
            event,
            action,
            issue_number,
            deps.clock(),
        ):
            return JSONResponse({"status": "duplicate"})

        if event != "issues":
            return JSONResponse({"status": "ignored", "reason": "event"})
        if action != "labeled":
            return JSONResponse({"status": "ignored", "reason": "action"})
        label_data = payload.get("label")
        if not isinstance(label_data, dict):
            return JSONResponse({"status": "ignored", "reason": "label"})
        if label_data.get("name") != deps.settings.trigger_label:
            return JSONResponse({"status": "ignored", "reason": "label"})
        repository = payload.get("repository")
        if not isinstance(repository, dict) or repository.get("full_name") != FORK_REPO:
            return JSONResponse({"status": "ignored", "reason": "repository"})
        if not isinstance(issue_data, dict):
            return JSONResponse({"status": "ignored", "reason": "pull_request"})
        if "pull_request" in issue_data:
            return JSONResponse({"status": "ignored", "reason": "pull_request"})
        state = issue_data.get("state")
        if isinstance(state, str) and state != "open":
            return JSONResponse({"status": "ignored", "reason": "closed"})
        if issue_number is None or issue_number <= 0:
            return JSONResponse({"error": "invalid json"}, status_code=400)

        title = issue_data.get("title")
        title = title if isinstance(title, str) else ""
        body = issue_data.get("body")
        body = body if isinstance(body, str) else ""
        labels_data = issue_data.get("labels")
        labels = (
            tuple(
                item["name"]
                for item in labels_data
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            )
            if isinstance(labels_data, list)
            else ()
        )
        state = state if isinstance(state, str) else "open"
        html_url = issue_data.get("html_url")
        html_url = html_url if isinstance(html_url, str) else ""
        issue = Issue(
            number=issue_number,
            title=title,
            body=body,
            labels=labels,
            state=state,
            html_url=html_url,
            repo=repository["full_name"],
        )
        now = deps.clock()
        new = deps.db.upsert_seen_issue(issue, now)
        deps.db.add_event(issue_number, "webhook_accepted", f"delivery {delivery_id}", now)
        deps.wake.set()
        return JSONResponse({"status": "accepted", "issue": issue_number, "new": new}, status_code=202)
