"""One-time admin bootstrap: create the two playbooks in the Devin org if no playbook has their title.

Needs DEVIN_API_KEY (a key with ManageOrgPlaybooks) and DEVIN_ORG_ID. Dry-run by default; pass --apply
to create. The runtime service key never needs this permission.

    python scripts/bootstrap_playbooks.py           # prints exists / would create
    python scripts/bootstrap_playbooks.py --apply   # creates the missing ones
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.devin_client import DevinError, HttpDevinClient
from app.models import Mode


@dataclass(frozen=True)
class PlaybookSpec:
    title: str
    macro: str
    body_path: Path
    schema_path: Path | None

    def body(self) -> str:
        return self.body_path.read_text()

    def schema(self) -> dict | None:
        return json.loads(self.schema_path.read_text()) if self.schema_path else None


SPECS = (
    PlaybookSpec(
        "CVE Reachability Triage (Read-Only)",
        "!cve_triage",
        ROOT / "playbooks" / "cve_triage.md",
        ROOT / "schemas" / "triage_output.json",
    ),
    PlaybookSpec(
        "Dependency Security Fix (superset)",
        "!dep_security_fix",
        ROOT / "playbooks" / "dep_security_fix.md",
        None,
    ),
)


async def run(settings: Settings, apply: bool, specs: Sequence[PlaybookSpec] = SPECS) -> int:
    client = HttpDevinClient(settings)
    try:
        existing = {playbook.title for playbook in await client.list_playbooks()}
        for spec in specs:
            if spec.title in existing:
                print(f"exists: {spec.title}")
            elif not apply:
                print(f"would create: {spec.title}")
            else:
                created = await client.create_playbook(spec.title, spec.body(), spec.macro, spec.schema())
                print(f"created: {spec.title} ({created.playbook_id})")
    finally:
        await client.aclose()
    return 0


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] = os.environ) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="create missing playbooks (default: dry run)")
    args = parser.parse_args(argv)
    missing = [name for name in ("DEVIN_API_KEY", "DEVIN_ORG_ID") if not env.get(name)]
    if missing:
        for name in missing:
            print(f"{name}: missing")
        return 2
    settings = Settings.from_env({**env, "APP_MODE": Mode.LIVE.value})
    try:
        return asyncio.run(run(settings, args.apply))
    except DevinError as exc:
        print(f"error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
