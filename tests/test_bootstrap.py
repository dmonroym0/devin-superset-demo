import importlib.util
import json
import sys
from pathlib import Path

import httpx
import respx

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "bootstrap_playbooks", ROOT / "scripts" / "bootstrap_playbooks.py"
)
bootstrap = importlib.util.module_from_spec(spec)
sys.modules["bootstrap_playbooks"] = bootstrap
spec.loader.exec_module(bootstrap)

BASE = "https://devin.test"
ORG = "/v3/organizations/org-test"
KEY = "admin-key-not-real"
ENV = {"DEVIN_API_KEY": KEY, "DEVIN_ORG_ID": "org-test", "DEVIN_API_BASE": BASE}
TRIAGE = "CVE Reachability Triage (Read-Only)"
FIX = "Dependency Security Fix (superset)"


def listing(*titles):
    items = [{"playbook_id": f"pb-{i}", "title": title} for i, title in enumerate(titles)]
    return httpx.Response(200, json={"items": items, "has_next_page": False})


@respx.mock(base_url=BASE, assert_all_called=False)
def test_existing_playbooks_are_not_created(respx_mock, capsys):
    respx_mock.get(f"{ORG}/playbooks").mock(return_value=listing(TRIAGE, FIX))
    post = respx_mock.post(f"{ORG}/playbooks")
    assert bootstrap.main(["--apply"], ENV) == 0
    assert not post.called
    out = capsys.readouterr().out
    assert f"exists: {TRIAGE}" in out and f"exists: {FIX}" in out and KEY not in out


@respx.mock(base_url=BASE, assert_all_called=False)
def test_missing_playbook_created_with_apply(respx_mock, capsys):
    respx_mock.get(f"{ORG}/playbooks").mock(return_value=listing(FIX))
    post = respx_mock.post(f"{ORG}/playbooks").mock(
        return_value=httpx.Response(200, json={"playbook_id": "pb-new", "title": TRIAGE})
    )
    assert bootstrap.main(["--apply"], ENV) == 0
    assert post.call_count == 1
    body = json.loads(post.calls.last.request.content)
    assert body["title"] == TRIAGE and body["macro"] == "!cve_triage"
    assert body["body"] == (ROOT / "playbooks" / "cve_triage.md").read_text()
    assert body["structured_output_schema"] == json.loads(
        (ROOT / "schemas" / "triage_output.json").read_text()
    )
    assert post.calls.last.request.headers["Authorization"] == f"Bearer {KEY}"
    assert KEY not in capsys.readouterr().out


@respx.mock(base_url=BASE, assert_all_called=False)
def test_dry_run_does_not_post(respx_mock, capsys):
    respx_mock.get(f"{ORG}/playbooks").mock(return_value=listing())
    post = respx_mock.post(f"{ORG}/playbooks")
    assert bootstrap.main([], ENV) == 0
    assert not post.called
    out = capsys.readouterr().out
    assert f"would create: {TRIAGE}" in out and f"would create: {FIX}" in out


def test_missing_env_reports_names_only(capsys):
    assert bootstrap.main([], {"DEVIN_API_KEY": KEY}) == 2
    out = capsys.readouterr().out
    assert "DEVIN_ORG_ID: missing" in out and KEY not in out
