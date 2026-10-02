from test_pipeline import RecordingActions, make_deps, run_ticks, seed

from app.models import Stage


async def test_unset_mode_is_omitted(tmp_path):
    deps = await make_deps(tmp_path)
    seed(deps, 1)
    await run_ticks(deps, RecordingActions(), ticks=6)
    assert {"stage-triage", "stage-fix"} <= {t for r in deps.devin.requests for t in r.tags}
    assert all("devin_mode" not in r.to_payload() for r in deps.devin.requests)
    assert all(row.devin_mode is None for row in deps.db.list_sessions())


async def test_configured_modes_are_sent_per_stage(tmp_path):
    deps = await make_deps(tmp_path, DEVIN_MODE_TRIAGE="fast", DEVIN_MODE_FIX="ultra")
    seed(deps, 1)
    await run_ticks(deps, RecordingActions(), ticks=6)
    modes = {
        ("stage-triage" if "stage-triage" in r.tags else "stage-fix"): r.to_payload()["devin_mode"]
        for r in deps.devin.requests
    }
    assert modes == {"stage-triage": "fast", "stage-fix": "ultra"}
    assert {row.stage: row.devin_mode for row in deps.db.list_sessions()} == {
        Stage.TRIAGE: "fast",
        Stage.FIX: "ultra",
    }
