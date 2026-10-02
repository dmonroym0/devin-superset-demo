"""Resolve the triage and fix playbooks (by ID override or exact title) and check the triage schema."""

from __future__ import annotations

from app.config import Settings
from app.interfaces import DevinClient, ResolvedPlaybooks
from app.models import Playbook
from app.schema_check import load_local_triage_schema, schema_diff


class PlaybookError(RuntimeError):
    pass


class SchemaMismatch(PlaybookError):
    def __init__(self, path: str):
        self.path = path
        super().__init__(f"triage schema differs from playbook at {path}")


async def _resolve_id(devin: DevinClient, override: str | None, title: str, listed: list[Playbook] | None):
    if override:
        return override, listed
    if listed is None:
        listed = await devin.list_playbooks()
    matches = [playbook for playbook in listed if playbook.title == title]
    if not matches:
        raise PlaybookError(f"no playbook titled {title!r}")
    if len(matches) > 1:
        raise PlaybookError(f"ambiguous: {len(matches)} playbooks titled {title!r}")
    return matches[0].playbook_id, listed


async def resolve_playbooks(settings: Settings, devin: DevinClient) -> ResolvedPlaybooks:
    triage_id, listed = await _resolve_id(
        devin, settings.playbook_triage_id, settings.playbook_triage_title, None
    )
    fix_id, _ = await _resolve_id(devin, settings.playbook_fix_id, settings.playbook_fix_title, listed)
    triage = await devin.get_playbook(triage_id)
    fix = await devin.get_playbook(fix_id)
    if triage.structured_output_schema is None:
        raise PlaybookError(f"triage playbook {triage.title!r} has no structured_output_schema")
    path = schema_diff(load_local_triage_schema(), triage.structured_output_schema)
    if path:
        raise SchemaMismatch(path)
    return ResolvedPlaybooks(triage=triage, fix=fix)
