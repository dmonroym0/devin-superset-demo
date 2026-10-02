import sqlite3

import pytest

from app.db import Database
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
