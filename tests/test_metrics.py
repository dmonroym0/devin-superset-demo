from app.budget import Budget
from app.config import Settings
from app.db import Database
from app.metrics import compute
from app.models import Issue, IssueState


def test_metrics_rate_and_median_time_to_pr():
    db = Database(":memory:")
    db.init_schema()
    budget = Budget(db, ceiling=120)
    settings = Settings.from_env({})
    issues = [
        Issue(number=1, title="One", body=""),
        Issue(number=2, title="Two", body=""),
        Issue(number=3, title="Three", body=""),
        Issue(number=4, title="Four", body=""),
    ]
    for issue, first_seen in zip(issues, [0, 10, 20, 30], strict=True):
        db.upsert_seen_issue(issue, first_seen)

    db.transition(
        1, [IssueState.SEEN], IssueState.PR_OPENED, 100, pr_url="https://example/1", pr_opened_at=100
    )
    db.transition(
        2, [IssueState.SEEN], IssueState.PR_OPENED, 110, pr_url="https://example/2", pr_opened_at=50
    )
    db.transition(3, [IssueState.SEEN], IssueState.NOT_REACHABLE, 120)
    db.transition(4, [IssueState.SEEN], IssueState.NEEDS_HUMAN, 130)

    metrics = compute(db, budget, settings, now=200)
    assert metrics["automation_rate"] == 0.75
    assert metrics["median_time_to_pr_s"] == 70.0
    assert metrics["issues"] == {
        "seen": 4,
        "in_flight": 0,
        "pr_opened": 2,
        "needs_human": 1,
        "not_reachable": 1,
        "queued_budget": 0,
        "error": 0,
    }

    db.close()


def test_metrics_null_rates_on_empty_database():
    db = Database(":memory:")
    db.init_schema()
    settings = Settings.from_env({})
    metrics = compute(db, Budget(db, ceiling=120), settings, now=0)

    assert metrics["automation_rate"] is None
    assert metrics["median_time_to_pr_s"] is None
    assert metrics["issues"]["seen"] == 0
    assert metrics["generated_at"] == "1970-01-01T00:00:00+00:00"

    db.close()
