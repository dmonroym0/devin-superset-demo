import json
from pathlib import Path

from jsonschema import validate

ROOT = Path(__file__).parents[1]


def test_seed_issue_numbers_and_content():
    seed = json.loads((ROOT / "app/demo/seed_issues.json").read_text())

    assert [issue["number"] for issue in seed["issues"]] == [1, 2, 3, 4, 5]
    assert all(issue["title"].strip() and issue["body"].strip() for issue in seed["issues"])


def test_every_non_null_scenario_triage_output_validates():
    scenarios = json.loads((ROOT / "app/demo/scenarios.json").read_text())
    schema = json.loads((ROOT / "schemas/triage_output.json").read_text())

    scripted = [*scenarios["issues"].values(), *scenarios["synthetic"].values()]
    outputs = [
        scenario["triage"]["structured_output"]
        for scenario in scripted
        if scenario.get("triage", {}).get("structured_output") is not None
    ]
    assert outputs
    for output in outputs:
        validate(instance=output, schema=schema)
