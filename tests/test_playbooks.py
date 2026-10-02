import copy

import pytest
from fastapi import FastAPI
from test_pipeline import RecordingActions, make_deps

from app.config import Settings
from app.fake_devin import FakeDevin
from app.models import Playbook
from app.pipeline import register
from app.playbooks import PlaybookError, SchemaMismatch, resolve_playbooks
from app.schema_check import load_local_triage_schema

SETTINGS = Settings.from_env({})


class CountingDevin(FakeDevin):
    def __init__(self):
        super().__init__({})
        self.list_calls = 0
        self.get_calls: list[str] = []

    async def list_playbooks(self):
        self.list_calls += 1
        return await super().list_playbooks()

    async def get_playbook(self, playbook_id):
        self.get_calls.append(playbook_id)
        return await super().get_playbook(playbook_id)


async def test_resolves_by_title_and_uses_get_schema():
    devin = CountingDevin()
    resolved = await resolve_playbooks(SETTINGS, devin)
    assert (resolved.triage.playbook_id, resolved.fix.playbook_id) == (
        "playbook-demo-triage",
        "playbook-demo-fix",
    )
    assert resolved.triage.structured_output_schema == load_local_triage_schema()
    assert devin.list_calls == 1 and devin.get_calls == ["playbook-demo-triage", "playbook-demo-fix"]


async def test_id_overrides_skip_listing():
    devin = CountingDevin()
    devin.playbooks["pb-x"] = Playbook("pb-x", "Other", "!x", load_local_triage_schema())
    settings = Settings.from_env({"PLAYBOOK_TRIAGE_ID": "pb-x", "PLAYBOOK_FIX_ID": "playbook-demo-fix"})
    resolved = await resolve_playbooks(settings, devin)
    assert resolved.triage.playbook_id == "pb-x" and devin.list_calls == 0


async def test_missing_title():
    devin = CountingDevin()
    with pytest.raises(PlaybookError, match="no playbook titled"):
        await resolve_playbooks(Settings.from_env({"PLAYBOOK_FIX_TITLE": "Nope"}), devin)


async def test_ambiguous_title():
    devin = CountingDevin()
    devin.playbooks["dup"] = Playbook("dup", SETTINGS.playbook_triage_title)
    with pytest.raises(PlaybookError, match="ambiguous"):
        await resolve_playbooks(SETTINGS, devin)


async def test_triage_without_schema():
    devin = CountingDevin()
    devin.playbooks["playbook-demo-triage"] = Playbook("playbook-demo-triage", SETTINGS.playbook_triage_title)
    with pytest.raises(PlaybookError, match="no structured_output_schema"):
        await resolve_playbooks(SETTINGS, devin)


async def test_schema_mismatch_reports_path():
    devin = CountingDevin()
    schema = copy.deepcopy(load_local_triage_schema())
    schema["required"] = [*schema.get("required", []), "extra"]
    devin.playbooks["playbook-demo-triage"] = Playbook(
        "playbook-demo-triage", SETTINGS.playbook_triage_title, "!t", schema
    )
    with pytest.raises(SchemaMismatch) as info:
        await resolve_playbooks(SETTINGS, devin)
    assert info.value.path.startswith("required")
    assert str(info.value) == f"triage schema differs from playbook at {info.value.path}"


async def test_live_startup_hook_raises_on_mismatch(tmp_path):
    deps = await make_deps(tmp_path)
    deps.playbooks = None
    deps.settings = Settings.from_env(
        {"APP_MODE": "live", "DEVIN_ORG_ID": "org-test", "PLAYBOOK_FIX_TITLE": "Nope"}
    )
    register(FastAPI(), deps, RecordingActions())
    with pytest.raises(PlaybookError):
        await deps.startup[0]()
    assert deps.playbooks is None
