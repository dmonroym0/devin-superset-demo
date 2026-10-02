import copy
import io
from urllib.parse import parse_qs

import httpx

from app.preflight import main
from app.schema_check import load_local_triage_schema

SENTINELS = (
    "ghp_SENTINEL_TOKEN",
    "devin_SENTINEL_KEY",
    "org-SENTINELORG",
    "playbook-SENTINELID",
    "whsec_SENTINEL_WEBHOOK",
)
TRIAGE_TITLE = "CVE Reachability Triage (Read-Only)"
FIX_TITLE = "Dependency Security Fix (superset)"


def run(env, handler):
    output = io.StringIO()
    code = main(env, transport=httpx.MockTransport(handler), out=output)
    text = output.getvalue()
    assert all(sentinel not in text for sentinel in SENTINELS)
    return code, text


def live_env(**overrides):
    env = {
        "APP_MODE": "live",
        "GITHUB_TOKEN": "ghp_SENTINEL_TOKEN",
        "DEVIN_API_KEY": "devin_SENTINEL_KEY",
        "DEVIN_ORG_ID": "org-SENTINELORG",
        "GITHUB_WEBHOOK_SECRET": "whsec_SENTINEL_WEBHOOK",
    }
    env.update(overrides)
    return env


def api_handler(request):
    if request.url.host == "api.github.com":
        return httpx.Response(200, json={})
    if request.url.path.endswith("/playbooks"):
        return httpx.Response(
            200,
            json={
                "items": [
                    {"playbook_id": "playbook-SENTINELID", "title": TRIAGE_TITLE},
                    {"playbook_id": "fix-id", "title": FIX_TITLE},
                ],
                "end_cursor": None,
                "has_next_page": False,
                "total": 2,
            },
        )
    playbook_id = request.url.path.rsplit("/", 1)[-1]
    schema = load_local_triage_schema() if playbook_id == "playbook-SENTINELID" else None
    return httpx.Response(
        200,
        json={"playbook_id": playbook_id, "title": "playbook", "structured_output_schema": schema},
    )


def test_demo_without_keys_succeeds():
    code, output = run({}, lambda request: httpx.Response(500))
    assert code == 0
    assert "APP_MODE: set (demo)" in output
    assert "DEMO_SEED: set" in output
    assert "DEMO_SCENARIOS: set" in output
    assert "TRIAGE_SCHEMA: set (local copy)" in output
    assert "GITHUB_TOKEN: missing (not needed in DEMO)" in output
    assert "preflight: ok" in output


def test_invalid_mode_fails():
    code, output = run({"APP_MODE": "bogus"}, lambda request: httpx.Response(500))
    assert code == 1
    assert "APP_MODE invalid" in output
    assert "preflight: failed" in output


def test_live_missing_credentials_are_reported():
    code, output = run({"APP_MODE": "live"}, lambda request: httpx.Response(500))
    assert code == 1
    assert "GITHUB_TOKEN: missing" in output
    assert "DEVIN_API_KEY: missing" in output
    assert "DEVIN_ORG_ID: missing" in output
    assert "preflight: failed" in output


def test_github_unauthorized_fails_without_printing_values():
    code, output = run(
        live_env(),
        lambda request: httpx.Response(401),
    )
    assert code == 1
    assert "GITHUB_TOKEN: invalid (GitHub returned 401)" in output


def test_devin_org_not_found_is_reported():
    def handler(request):
        if request.url.host == "api.github.com":
            return httpx.Response(200, json={})
        return httpx.Response(404, json={})

    code, output = run(live_env(), handler)
    assert code == 1
    assert "DEVIN_ORG_ID: invalid (Devin returned 404)" in output


def test_devin_unauthorized_is_reported():
    def handler(request):
        if request.url.host == "api.github.com":
            return httpx.Response(200, json={})
        return httpx.Response(401, json={})

    code, output = run(live_env(), handler)
    assert code == 1
    assert "DEVIN_API_KEY: invalid (Devin returned 401)" in output


def test_missing_playbook_title_is_reported():
    def handler(request):
        if request.url.host == "api.github.com":
            return httpx.Response(200, json={})
        return httpx.Response(
            200,
            json={"items": [], "end_cursor": None, "has_next_page": False, "total": 0},
        )

    code, output = run(live_env(), handler)
    assert code == 1
    assert 'PLAYBOOK_TRIAGE: invalid (no playbook titled "CVE Reachability Triage (Read-Only)")' in output


def test_devin_pagination_finds_playbook_on_second_page():
    requested_cursors = []

    def handler(request):
        if request.url.host == "api.github.com":
            return httpx.Response(200, json={})
        if request.url.path.endswith("/playbooks"):
            params = parse_qs(request.url.query.decode())
            requested_cursors.append(params.get("after", [None])[0])
            if params.get("after") == ["next-page"]:
                return httpx.Response(
                    200,
                    json={
                        "items": [
                            {"playbook_id": "playbook-SENTINELID", "title": TRIAGE_TITLE},
                            {"playbook_id": "fix-id", "title": FIX_TITLE},
                        ],
                        "end_cursor": None,
                        "has_next_page": False,
                        "total": 3,
                    },
                )
            return httpx.Response(
                200,
                json={
                    "items": [{"playbook_id": "other", "title": "Other"}],
                    "end_cursor": "next-page",
                    "has_next_page": True,
                    "total": 3,
                },
            )
        playbook_id = request.url.path.rsplit("/", 1)[-1]
        schema = load_local_triage_schema() if playbook_id == "playbook-SENTINELID" else None
        return httpx.Response(200, json={"structured_output_schema": schema})

    code, output = run(live_env(), handler)
    assert code == 0
    assert requested_cursors == [None, "next-page"]
    assert "PLAYBOOK_TRIAGE: set" in output


def test_triage_schema_difference_reports_json_path():
    changed_schema = copy.deepcopy(load_local_triage_schema())
    changed_schema["properties"]["cves"]["items"]["properties"]["confidence"]["enum"] = ["high"]

    def handler(request):
        if request.url.host == "api.github.com":
            return httpx.Response(200, json={})
        if request.url.path.endswith("/playbooks"):
            return api_handler(request)
        playbook_id = request.url.path.rsplit("/", 1)[-1]
        schema = changed_schema if playbook_id == "playbook-SENTINELID" else None
        return httpx.Response(200, json={"structured_output_schema": schema})

    code, output = run(live_env(), handler)
    assert code == 1
    assert (
        "TRIAGE_SCHEMA: invalid (differs from playbook at properties.cves.items.properties.confidence.enum)"
    ) in output


def test_configured_playbook_id_is_fetched():
    fetched_ids = []

    def handler(request):
        if request.url.host == "api.github.com":
            return httpx.Response(200, json={})
        if request.url.path.endswith("/playbooks"):
            return httpx.Response(
                200,
                json={"items": [], "end_cursor": None, "has_next_page": False, "total": 0},
            )
        fetched_ids.append(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(
            200,
            json={"structured_output_schema": load_local_triage_schema()},
        )

    code, output = run(
        live_env(PLAYBOOK_TRIAGE_ID="playbook-SENTINELID", PLAYBOOK_FIX_ID="fix-override"),
        handler,
    )
    assert code == 0
    assert fetched_ids == ["playbook-SENTINELID", "fix-override"]
    assert "PLAYBOOK_TRIAGE: set" in output


def test_connect_error_is_redacted():
    def handler(request):
        raise httpx.ConnectError(
            "boom ghp_SENTINEL_TOKEN org-SENTINELORG",
            request=request,
        )

    code, output = run(live_env(), handler)
    assert code == 1
    assert "GITHUB_TOKEN: invalid (unreachable)" in output


def test_live_success_with_webhook_secret():
    code, output = run(live_env(), api_handler)
    assert code == 0
    assert "GITHUB_TOKEN: set (repo, issues, labels readable)" in output
    assert "DEVIN_API_KEY: set" in output
    assert "DEVIN_ORG_ID: set" in output
    assert "GITHUB_WEBHOOK_SECRET: set" in output
    assert "TRIAGE_SCHEMA: set (matches playbook)" in output
    assert "FIX_SCHEMA: missing (optional)" in output
    assert "preflight: ok" in output
