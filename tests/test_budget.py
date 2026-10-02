from concurrent.futures import ThreadPoolExecutor

from app.budget import Budget
from app.db import Database
from app.models import Stage


def make_budget(tmp_path, ceiling=120):
    db = Database(str(tmp_path / "budget.db"))
    db.init_schema()
    return db, Budget(db, ceiling=ceiling)


def test_budget_accepts_exact_ceiling_then_refuses(tmp_path):
    db, budget = make_budget(tmp_path)

    for issue_number in range(1, 9):
        assert budget.reserve(issue_number, Stage.FIX, 15, 100.0) is not None
    assert budget.committed() == 120
    assert budget.remaining() == 0
    assert budget.reserve(9, Stage.TRIAGE, 5, 101.0) is None

    db.close()


def test_cancel_frees_reserved_cap(tmp_path):
    db, budget = make_budget(tmp_path, ceiling=20)
    reservation_id = budget.reserve(1, Stage.FIX, 15, 100.0)
    assert reservation_id is not None
    assert budget.cancel(reservation_id) is True
    assert budget.committed() == 0
    assert budget.reserve(2, Stage.FIX, 15, 101.0) is not None

    db.close()


def test_cancel_unattached_reservations_only_cancels_matching_issue_and_stage(tmp_path):
    db, budget = make_budget(tmp_path)
    assert budget.reserve(1, Stage.TRIAGE, 5, 100.0) is not None
    triage_attached = budget.reserve(1, Stage.TRIAGE, 5, 100.0)
    budget.attach(triage_attached, "triage-1")
    budget.reserve(1, Stage.FIX, 15, 100.0)
    budget.reserve(2, Stage.TRIAGE, 5, 100.0)

    assert budget.committed() == 30
    assert budget.cancel_unattached(1, Stage.TRIAGE) == 1
    assert budget.committed() == 25
    assert budget.cancel_unattached(1, Stage.TRIAGE) == 0

    db.close()


def test_default_stage_caps_are_five_and_fifteen(tmp_path):
    from app.config import Settings

    db, budget = make_budget(tmp_path)
    settings = Settings.from_env({})

    assert settings.triage_acu_cap == 5
    assert settings.fix_acu_cap == 15
    assert budget.reserve(1, Stage.TRIAGE, settings.triage_acu_cap, 100.0) is not None
    assert budget.reserve(1, Stage.FIX, settings.fix_acu_cap, 100.0) is not None
    assert budget.committed() == 20

    db.close()


def test_concurrent_reservations_never_exceed_ceiling(tmp_path):
    db, budget = make_budget(tmp_path, ceiling=50)

    def reserve(issue_number):
        return budget.reserve(issue_number, Stage.FIX, 15, 100.0)

    with ThreadPoolExecutor(max_workers=50) as pool:
        reservations = list(pool.map(reserve, range(1, 51)))

    assert sum(reservation is not None for reservation in reservations) == 3
    assert budget.committed() == 45
    assert budget.committed() <= budget.ceiling

    db.close()
