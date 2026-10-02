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
