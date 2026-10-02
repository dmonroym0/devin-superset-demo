import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
FEATURES = ROOT / "features.md"
COMMAND = re.compile(r"`(?:python -m )?pytest ([^`]+)`")


def _rows() -> list[list[str]]:
    rows = []
    for line in FEATURES.read_text().splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 5 and cells[1] in {"planned", "built", "verified"}:
            rows.append(cells)
    return rows


def _pytest_targets() -> list[str]:
    targets = set()
    for row in _rows():
        for command in COMMAND.findall(row[3]):
            targets.update(token for token in command.split() if token.startswith("tests/"))
    return sorted(targets)


def test_features_md_pytest_commands_collect_without_cwd_on_sys_path():
    files = sorted({target.split("::")[0] for target in _pytest_targets()})
    assert files
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    failures = {}
    for path in files:
        result = subprocess.run(
            [sys.executable, "-P", "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", path],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode != 0:
            failures[path] = result.stdout[-500:]
    assert failures == {}


def test_planned_rows_name_files_that_do_not_exist_yet():
    shipped = []
    for name, status, files, _how, _req in _rows():
        paths = [path.strip(" `") for path in files.split(",") if path.strip(" `")]
        if status == "planned" and paths and all((ROOT / path).exists() for path in paths):
            shipped.append(name)
    assert shipped == []
