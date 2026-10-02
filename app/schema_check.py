"""Compare the repo's local schema copy with the schema attached to a playbook (the source of truth)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

LOCAL_TRIAGE_SCHEMA = Path(__file__).resolve().parent.parent / "schemas" / "triage_output.json"


def load_local_triage_schema(path: Path = LOCAL_TRIAGE_SCHEMA) -> dict[str, Any]:
    return json.loads(path.read_text())


def schema_diff(local: Any, remote: Any, path: str = "") -> str | None:
    """Return the first JSON path where the two schemas differ, or None if equal.

    Paths are dotted (`properties.cves.items.properties.confidence.enum`), list indices as `[i]`,
    and `<root>` for the top level. Keys are compared in sorted order so the result is deterministic.
    """
    here = path or "<root>"
    if isinstance(local, dict) and isinstance(remote, dict):
        for key in sorted(set(local) | set(remote), key=str):
            child = f"{path}.{key}" if path else str(key)
            if key not in local or key not in remote:
                return child
            found = schema_diff(local[key], remote[key], child)
            if found:
                return found
        return None
    if isinstance(local, list) and isinstance(remote, list):
        if len(local) != len(remote):
            return here
        for index, (left, right) in enumerate(zip(local, remote, strict=True)):
            found = schema_diff(left, right, f"{path}[{index}]")
            if found:
                return found
        return None
    if type(local) is not type(remote) or local != remote:
        return here
    return None
