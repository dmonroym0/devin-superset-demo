import sqlite3

import pytest

from app.db import Database, DatabaseModeMismatch
from app.models import Issue, IssueState


def test_delivery_deduplication(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.init_schema()

    assert db.record_delivery("delivery-1", "issues", "labeled", 4, 100.0) is True
    assert db.record_delivery("delivery-1", "issues", "labeled", 4, 101.0) is False

    db.close()


def test_upsert_issue_is_idempotent_and_refreshes_title(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.init_schema()
    issue = Issue(number=4, title="Initial", body="body")

    assert db.upsert_seen_issue(issue, 100.0) is True
    assert db.upsert_seen_issue(Issue(number=4, title="Updated", body="new"), 200.0) is False

    row = db.get_issue(4)
    assert row.title == "Updated"
    assert row.state is IssueState.SEEN
    assert row.first_seen_at == 100.0

    db.close()


def test_upsert_revives_cancelled_issue_as_seen(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.init_schema()
    issue = Issue(number=4, title="Initial", body="body")
    db.upsert_seen_issue(issue, 100.0)
    db.transition(
        4,
        [IssueState.SEEN],
        IssueState.CANCELLED,
        110.0,
        route_reason="issue closed",
    )

    assert db.upsert_seen_issue(Issue(number=4, title="Relabeled", body="new"), 120.0) is True
    row = db.get_issue(4)
    assert row.state is IssueState.SEEN
    assert row.route_reason is None
    assert row.title == "Relabeled"

    db.close()


def test_init_schema_migrates_create_started_column(tmp_path):
    db = Database(str(tmp_path / "legacy.db"))
    db._connection.execute(
        "CREATE TABLE ledger (id INTEGER PRIMARY KEY, issue_number INTEGER NOT NULL, "
        "stage TEXT NOT NULL, cap INTEGER NOT NULL, session_id TEXT, created_at REAL NOT NULL, "
        "cancelled INTEGER NOT NULL DEFAULT 0)"
    )
    db._connection.commit()

    db.init_schema()

    columns = {row["name"] for row in db._connection.execute("PRAGMA table_info(ledger)")}
    assert "create_started_at" in columns

    db.close()


@pytest.mark.parametrize("mode", ["live", "demo"])
def test_claim_mode_rejects_legacy_database_with_issue_data(tmp_path, mode):
    path = str(tmp_path / f"legacy-{mode}.db")
    db = Database(path)
    db.init_schema()
    db.upsert_seen_issue(Issue(number=1, title="Legacy", body=""), 100.0)
    db.close()

    restarted = Database(path)
    with pytest.raises(DatabaseModeMismatch, match="has data but no mode marker"):
        restarted.claim_mode(mode)
    restarted.close()


def test_claim_mode_stamps_empty_database_and_allows_same_mode_with_data(tmp_path):
    path = str(tmp_path / "mode.db")
    db = Database(path)
    db.init_schema()
    db.claim_mode("demo")
    db.upsert_seen_issue(Issue(number=1, title="Issue", body=""), 100.0)
    db.close()

    restarted = Database(path)
    restarted.init_schema()
    restarted.claim_mode("demo")
    assert restarted.get_issue(1).title == "Issue"
    restarted.close()


def test_transition_is_compare_and_set(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.init_schema()
    db.upsert_seen_issue(Issue(number=4, title="Issue", body="body"), 100.0)

    assert db.transition(4, [IssueState.SEEN], IssueState.TRIAGING, 101.0) is True
    assert db.transition(4, [IssueState.SEEN], IssueState.TRIAGING, 102.0) is False
    assert db.get_issue(4).state is IssueState.TRIAGING

    db.close()


def test_transition_rejects_unknown_fields(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.init_schema()
    db.upsert_seen_issue(Issue(number=4, title="Issue", body="body"), 100.0)

    try:
        db.transition(4, [IssueState.SEEN], IssueState.TRIAGING, 101.0, body="not persisted")
    except ValueError as exc:
        assert "body" in str(exc)
    else:
        raise AssertionError("unknown transition field was accepted")

    db.close()


def test_accept_delivery_is_atomic_and_deduplicated(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.init_schema()
    db._connection.execute(
        "CREATE TRIGGER fail_issue BEFORE INSERT ON issues BEGIN SELECT RAISE(ABORT, 'boom'); END"
    )
    db._connection.commit()
    issue = Issue(number=42, title="Atomic delivery", body="body")

    with pytest.raises(sqlite3.IntegrityError, match="boom"):
        db.accept_delivery("delivery-atomic", "issues", "labeled", issue, 100.0)

    assert db.has_delivery("delivery-atomic") is False
    assert db.get_issue(42) is None
    assert db.list_events(issue_number=42) == []

    db._connection.execute("DROP TRIGGER fail_issue")
    db._connection.commit()
    assert db.accept_delivery("delivery-atomic", "issues", "labeled", issue, 101.0) == (True, True)
    assert db.get_issue(42).title == "Atomic delivery"
    events = db.list_events(issue_number=42)
    assert [(event.kind, event.detail) for event in events] == [
        ("webhook_accepted", "delivery delivery-atomic")
    ]
    assert db.accept_delivery("delivery-atomic", "issues", "labeled", issue, 102.0) == (False, False)

    db.close()
