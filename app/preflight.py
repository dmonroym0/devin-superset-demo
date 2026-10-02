"""Validate local assets and read-only API access before starting the service."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import TextIO

import httpx

from app import schema_check
from app.config import ConfigError, Settings
from app.models import Mode


def _report(out: TextIO, name: str, status: str, reason: str | None = None) -> None:
    suffix = f" ({reason})" if reason else ""
    print(f"{name}: {status}{suffix}", file=out)


def _local_schema() -> dict | None:
    try:
        schema = schema_check.load_local_triage_schema()
    except (OSError, ValueError):
        return None
    return schema if isinstance(schema, dict) else None


def _check_github(client: httpx.Client, settings: Settings, out: TextIO) -> bool:
    headers = {
        "Authorization": f"Bearer {settings.github_token.get()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    base = settings.github_api_base.rstrip("/")
    endpoints = (
        (f"{base}/repos/{settings.github_repo}", None),
        (f"{base}/repos/{settings.github_repo}/issues?per_page=1", "issues"),
        (f"{base}/repos/{settings.github_repo}/labels?per_page=1", "labels"),
    )
    for url, resource in endpoints:
        try:
            response = client.get(url, headers=headers)
        except httpx.HTTPError:
            _report(out, "GITHUB_TOKEN", "invalid", "unreachable")
            return False
        if response.status_code != 200:
            if resource is None:
                reason = f"GitHub returned {response.status_code}"
            else:
                reason = f"cannot read {resource}: GitHub returned {response.status_code}"
            _report(out, "GITHUB_TOKEN", "invalid", reason)
            return False
    _report(out, "GITHUB_TOKEN", "set", "repo, issues, labels readable")
    return True


def _load_playbooks(client: httpx.Client, settings: Settings, out: TextIO) -> tuple[list[dict] | None, bool]:
    url = f"{settings.devin_api_base.rstrip('/')}/v3/organizations/{settings.devin_org_id}/playbooks"
    headers = {"Authorization": f"Bearer {settings.devin_api_key.get()}"}
    playbooks: list[dict] = []
    after: str | None = None
    for _ in range(50):
        params = {"first": 200}
        if after is not None:
            params["after"] = after
        try:
            response = client.get(url, headers=headers, params=params)
        except httpx.HTTPError:
            _report(out, "DEVIN_API_KEY", "invalid", "unreachable")
            return None, False
        if response.status_code != 200:
            if response.status_code == 404:
                _report(out, "DEVIN_ORG_ID", "invalid", "Devin returned 404")
            else:
                _report(
                    out,
                    "DEVIN_API_KEY",
                    "invalid",
                    f"Devin returned {response.status_code}",
                )
            return None, False
        try:
            payload = response.json()
        except ValueError:
            _report(out, "DEVIN_API_KEY", "invalid", "unexpected response")
            return None, False
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("items"), list)
            or not isinstance(payload.get("has_next_page"), bool)
            or not (payload.get("end_cursor") is None or isinstance(payload.get("end_cursor"), str))
            or any(
                not isinstance(item, dict)
                or not isinstance(item.get("playbook_id"), str)
                or not isinstance(item.get("title"), str)
                for item in payload["items"]
            )
        ):
            _report(out, "DEVIN_API_KEY", "invalid", "unexpected response")
            return None, False
        playbooks.extend(payload["items"])
        cursor = payload["end_cursor"]
        if not payload["has_next_page"] or not cursor:
            break
        after = cursor
    return playbooks, True


def _get_playbook(
    client: httpx.Client,
    settings: Settings,
    playbooks: list[dict],
    configured_id: str | None,
    title: str,
    check_name: str,
    out: TextIO,
) -> tuple[dict | None, bool]:
    if configured_id:
        playbook_id = configured_id
    else:
        matches = [book for book in playbooks if book["title"] == title]
        if not matches:
            _report(out, check_name, "invalid", f'no playbook titled "{title}"')
            return None, False
        if len(matches) > 1:
            _report(out, check_name, "invalid", f'{len(matches)} playbooks titled "{title}"')
            return None, False
        playbook_id = matches[0]["playbook_id"]
    url = (
        f"{settings.devin_api_base.rstrip('/')}/v3/organizations/{settings.devin_org_id}"
        f"/playbooks/{playbook_id}"
    )
    headers = {"Authorization": f"Bearer {settings.devin_api_key.get()}"}
    try:
        response = client.get(url, headers=headers)
    except httpx.HTTPError:
        _report(out, check_name, "invalid", "unreachable")
        return None, False
    if response.status_code != 200:
        _report(out, check_name, "invalid", f"Devin returned {response.status_code}")
        return None, False
    try:
        payload = response.json()
    except ValueError:
        _report(out, check_name, "invalid", "unreachable")
        return None, False
    if not isinstance(payload, dict):
        _report(out, check_name, "invalid", "unexpected response")
        return None, False
    _report(out, check_name, "set")
    return payload, True


def _check_devin(client: httpx.Client, settings: Settings, out: TextIO) -> bool:
    playbooks, list_ok = _load_playbooks(client, settings, out)
    if not list_ok or playbooks is None:
        return False
    _report(out, "DEVIN_API_KEY", "set")
    _report(out, "DEVIN_ORG_ID", "set")
    failures = False
    triage, triage_ok = _get_playbook(
        client,
        settings,
        playbooks,
        settings.playbook_triage_id,
        settings.playbook_triage_title,
        "PLAYBOOK_TRIAGE",
        out,
    )
    failures |= not triage_ok
    if triage_ok and triage is not None:
        remote_schema = triage.get("structured_output_schema")
        local_schema = _local_schema()
        if not isinstance(remote_schema, dict):
            _report(out, "TRIAGE_SCHEMA", "invalid", "playbook has no structured output schema")
            failures = True
        elif local_schema is None:
            _report(out, "TRIAGE_SCHEMA", "invalid", "local schema unavailable")
            failures = True
        else:
            difference = schema_check.schema_diff(local_schema, remote_schema)
            if difference is None:
                _report(out, "TRIAGE_SCHEMA", "set", "matches playbook")
            else:
                _report(out, "TRIAGE_SCHEMA", "invalid", f"differs from playbook at {difference}")
                failures = True
    fix, fix_ok = _get_playbook(
        client,
        settings,
        playbooks,
        settings.playbook_fix_id,
        settings.playbook_fix_title,
        "PLAYBOOK_FIX",
        out,
    )
    failures |= not fix_ok
    if fix_ok and fix is not None:
        if isinstance(fix.get("structured_output_schema"), dict):
            _report(out, "FIX_SCHEMA", "set")
        else:
            _report(out, "FIX_SCHEMA", "missing", "optional")
    return not failures


def main(
    env: Mapping[str, str] = os.environ,
    *,
    transport: httpx.BaseTransport | None = None,
    out: TextIO = sys.stdout,
) -> int:
    try:
        settings = Settings.from_env(env)
    except ConfigError as error:
        for problem in error.problems:
            print(problem, file=out)
        print("preflight: failed", file=out)
        return 1

    if settings.mode is Mode.DEMO:
        _report(out, "APP_MODE", "set", "demo")
        demo_dir = Path(__file__).parent / "demo"
        failed = False
        try:
            seed = json.loads((demo_dir / "seed_issues.json").read_text())
            seed_valid = (
                isinstance(seed, dict) and isinstance(seed.get("issues"), list) and len(seed["issues"]) == 5
            )
        except (OSError, ValueError):
            seed_valid = False
        if seed_valid:
            _report(out, "DEMO_SEED", "set")
        else:
            _report(out, "DEMO_SEED", "invalid")
            failed = True
        try:
            scenarios = json.loads((demo_dir / "scenarios.json").read_text())
            scenarios_valid = isinstance(scenarios, dict) and "issues" in scenarios
        except (OSError, ValueError):
            scenarios_valid = False
        if scenarios_valid:
            _report(out, "DEMO_SCENARIOS", "set")
        else:
            _report(out, "DEMO_SCENARIOS", "invalid")
            failed = True
        if _local_schema() is None:
            _report(out, "TRIAGE_SCHEMA", "invalid")
            failed = True
        else:
            _report(out, "TRIAGE_SCHEMA", "set", "local copy")
        for name, is_set in (
            ("GITHUB_TOKEN", bool(settings.github_token)),
            ("DEVIN_API_KEY", bool(settings.devin_api_key)),
            ("DEVIN_ORG_ID", bool(settings.devin_org_id)),
        ):
            _report(out, name, "set" if is_set else "missing", None if is_set else "not needed in DEMO")
        print(f"preflight: {'failed' if failed else 'ok'}", file=out)
        return int(failed)

    _report(out, "APP_MODE", "set", "live")
    if settings.upstream_sync_enabled:
        _report(
            out,
            "UPSTREAM_SYNC_ENABLED",
            "set",
            "token also needs Contents, Pull requests and Workflows write; not checkable read-only",
        )
    failed = False
    for name, is_set in (
        ("GITHUB_TOKEN", bool(settings.github_token)),
        ("DEVIN_API_KEY", bool(settings.devin_api_key)),
        ("DEVIN_ORG_ID", bool(settings.devin_org_id)),
    ):
        _report(out, name, "set" if is_set else "missing")
        failed |= not is_set
    _report(
        out,
        "GITHUB_WEBHOOK_SECRET",
        "set" if settings.github_webhook_secret else "missing",
        None if settings.github_webhook_secret else "webhook disabled; sweep only",
    )
    for name, mode in (
        ("DEVIN_MODE_TRIAGE", settings.devin_mode_triage),
        ("DEVIN_MODE_FIX", settings.devin_mode_fix),
    ):
        _report(out, name, "set" if mode else "unset", None if mode else "org default")

    with httpx.Client(transport=transport, timeout=10) as client:
        if settings.github_token:
            failed |= not _check_github(client, settings, out)
        if settings.devin_api_key and settings.devin_org_id:
            failed |= not _check_devin(client, settings, out)

    print(f"preflight: {'failed' if failed else 'ok'}", file=out)
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
