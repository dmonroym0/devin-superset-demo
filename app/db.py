"""SQLite persistence for issues, sessions, deliveries, and events."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from app.models import BumpKind, Issue, IssueState, Stage


class DatabaseModeMismatch(RuntimeError):
    pass


@dataclass(frozen=True)
class IssueRow:
    number: int
    title: str
    state: IssueState
    package: str | None
    current_version: str | None
    fixed_version: str | None
    bump_kind: BumpKind | None
    route_reason: str | None
    pr_url: str | None
    last_error: str | None
    first_seen_at: float
    updated_at: float
    triaged_at: float | None
    pr_opened_at: float | None


@dataclass(frozen=True)
class SessionRow:
    session_id: str
    issue_number: int
    stage: Stage
    status: str
    status_detail: str | None
    devin_mode: str | None
    max_acu_limit: int
    acus_consumed: float
    url: str | None
    created_at: float
    updated_at: float
    settled_at: float | None = None
    archived: bool = False
    nudged: bool = False
    structured_output: dict[str, Any] | None = None


@dataclass(frozen=True)
class EventRow:
    id: int
    issue_number: int | None
    kind: str
    detail: str
    created_at: float


class Database:
    _ISSUE_FIELDS: ClassVar[set[str]] = {
        "package",
        "current_version",
        "fixed_version",
        "bump_kind",
        "route_reason",
        "pr_url",
        "last_error",
        "triaged_at",
        "pr_opened_at",
    }
    _SESSION_FIELDS: ClassVar[set[str]] = {
        "issue_number",
        "stage",
        "status",
        "status_detail",
        "devin_mode",
        "max_acu_limit",
        "acus_consumed",
        "url",
        "created_at",
        "updated_at",
        "settled_at",
        "archived",
        "nudged",
        "structured_output",
    }

    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._lock = threading.Lock()

    def init_schema(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS deliveries (
            delivery_id TEXT PRIMARY KEY,
            event TEXT NOT NULL,
            action TEXT NOT NULL,
            issue_number INTEGER,
            received_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS issues (
            number INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            state TEXT NOT NULL,
            package TEXT,
            current_version TEXT,
            fixed_version TEXT,
            bump_kind TEXT,
            route_reason TEXT,
            pr_url TEXT,
            last_error TEXT,
            first_seen_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            triaged_at REAL,
            pr_opened_at REAL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            issue_number INTEGER NOT NULL,
            stage TEXT NOT NULL,
            status TEXT NOT NULL,
            status_detail TEXT,
            devin_mode TEXT,
            max_acu_limit INTEGER NOT NULL,
            acus_consumed REAL NOT NULL,
            url TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            settled_at REAL,
            archived INTEGER NOT NULL DEFAULT 0,
            nudged INTEGER NOT NULL DEFAULT 0,
            structured_output TEXT
        );
        CREATE TABLE IF NOT EXISTS ledger (
            id INTEGER PRIMARY KEY,
            issue_number INTEGER NOT NULL,
            stage TEXT NOT NULL,
            cap INTEGER NOT NULL,
            session_id TEXT,
            created_at REAL NOT NULL,
            cancelled INTEGER NOT NULL DEFAULT 0,
            create_started_at REAL
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY,
            issue_number INTEGER,
            kind TEXT NOT NULL,
            detail TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS upstream_syncs (
            id INTEGER PRIMARY KEY,
            started_at REAL NOT NULL,
            outcome TEXT NOT NULL,
            before_sha TEXT,
            after_sha TEXT,
            detail TEXT,
            pr_url TEXT,
            issue_number INTEGER
        );
        """
        with self._lock:
            self._connection.executescript(schema)
            columns = {row["name"] for row in self._connection.execute("PRAGMA table_info(ledger)")}
            if "create_started_at" not in columns:
                self._connection.execute("ALTER TABLE ledger ADD COLUMN create_started_at REAL")
                self._connection.commit()

    def claim_mode(self, mode: str) -> None:
        with self._lock:
            row = self._connection.execute("SELECT value FROM meta WHERE key='mode'").fetchone()
            if row is None:
                has_data = self._connection.execute(
                    "SELECT EXISTS (SELECT 1 FROM issues) OR EXISTS (SELECT 1 FROM ledger) "
                    "OR EXISTS (SELECT 1 FROM sessions)"
                ).fetchone()[0]
                if has_data:
                    raise DatabaseModeMismatch(
                        f"database {self.path} has data but no mode marker; refusing to start in {mode}; "
                        "use a fresh DB_PATH/DATA_DIR"
                    )
                self._connection.execute("INSERT INTO meta(key, value) VALUES ('mode', ?)", (mode,))
                self._connection.commit()
                return
            stored = row["value"]
            if stored != mode:
                raise DatabaseModeMismatch(
                    f"database {self.path} belongs to {stored} mode, refusing to start in {mode}; "
                    "use a different DB_PATH/DATA_DIR"
                )

    def get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self._connection.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            self._connection.commit()

    def delete_meta(self, key: str) -> None:
        with self._lock:
            self._connection.execute("DELETE FROM meta WHERE key=?", (key,))
            self._connection.commit()

    def record_upstream_sync(
        self,
        started_at: float,
        outcome: str,
        before_sha: str | None = None,
        after_sha: str | None = None,
        detail: str | None = None,
        pr_url: str | None = None,
        issue_number: int | None = None,
    ) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT INTO upstream_syncs(started_at, outcome, before_sha, after_sha, detail, pr_url, issue_number) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (started_at, outcome, before_sha, after_sha, detail, pr_url, issue_number),
            )
            self._connection.commit()

    def latest_upstream_sync(self) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM upstream_syncs ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def record_delivery(
        self, delivery_id: str, event: str, action: str, issue_number: int | None, now: float
    ) -> bool:
        with self._lock:
            cursor = self._connection.execute(
                "INSERT OR IGNORE INTO deliveries(delivery_id, event, action, issue_number, received_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (delivery_id, event, action, issue_number, now),
            )
            self._connection.commit()
            return cursor.rowcount == 1

    def has_delivery(self, delivery_id: str) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT 1 FROM deliveries WHERE delivery_id=?", (delivery_id,)
            ).fetchone()
        return row is not None

    def accept_delivery(
        self,
        delivery_id: str,
        event: str,
        action: str,
        issue: Issue,
        now: float,
    ) -> tuple[bool, bool]:
        with self._lock:
            try:
                delivery = self._connection.execute(
                    "INSERT OR IGNORE INTO deliveries(delivery_id, event, action, issue_number, received_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (delivery_id, event, action, issue.number, now),
                )
                if delivery.rowcount != 1:
                    self._connection.rollback()
                    return False, False
                issue_cursor = self._connection.execute(
                    "INSERT OR IGNORE INTO issues(number, title, state, first_seen_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (issue.number, issue.title, IssueState.SEEN.value, now, now),
                )
                is_new_issue = issue_cursor.rowcount == 1
                if not is_new_issue:
                    existing = self._connection.execute(
                        "SELECT state FROM issues WHERE number=?", (issue.number,)
                    ).fetchone()
                    if existing["state"] == IssueState.CANCELLED.value:
                        self._connection.execute(
                            "UPDATE issues SET title=?, state=?, route_reason=NULL, updated_at=? "
                            "WHERE number=?",
                            (issue.title, IssueState.SEEN.value, now, issue.number),
                        )
                        is_new_issue = True
                    else:
                        self._connection.execute(
                            "UPDATE issues SET title=? WHERE number=?", (issue.title, issue.number)
                        )
                self._connection.execute(
                    "INSERT INTO events(issue_number, kind, detail, created_at) VALUES (?, ?, ?, ?)",
                    (issue.number, "webhook_accepted", f"delivery {delivery_id}", now),
                )
                self._connection.commit()
                return True, is_new_issue
            except Exception:
                self._connection.rollback()
                raise

    def upsert_seen_issue(self, issue: Issue, now: float) -> bool:
        with self._lock:
            cursor = self._connection.execute(
                "INSERT OR IGNORE INTO issues(number, title, state, first_seen_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (issue.number, issue.title, IssueState.SEEN.value, now, now),
            )
            inserted = cursor.rowcount == 1
            if not inserted:
                existing = self._connection.execute(
                    "SELECT state FROM issues WHERE number=?", (issue.number,)
                ).fetchone()
                if existing["state"] == IssueState.CANCELLED.value:
                    self._connection.execute(
                        "UPDATE issues SET title=?, state=?, route_reason=NULL, updated_at=? WHERE number=?",
                        (issue.title, IssueState.SEEN.value, now, issue.number),
                    )
                    inserted = True
                else:
                    self._connection.execute(
                        "UPDATE issues SET title=? WHERE number=?", (issue.title, issue.number)
                    )
            self._connection.commit()
            return inserted

    def get_issue(self, number: int) -> IssueRow | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM issues WHERE number=?", (number,)).fetchone()
        return _issue_row(row) if row else None

    def list_issues(self, states: Collection[IssueState] | None = None) -> list[IssueRow]:
        with self._lock:
            if states is None:
                rows = self._connection.execute("SELECT * FROM issues ORDER BY number").fetchall()
            else:
                state_values = [state.value for state in states]
                if not state_values:
                    return []
                placeholders = ",".join("?" for _ in state_values)
                rows = self._connection.execute(
                    f"SELECT * FROM issues WHERE state IN ({placeholders}) ORDER BY number",
                    state_values,
                ).fetchall()
        return [_issue_row(row) for row in rows]

    def _transition_update(
        self,
        number: int,
        from_states: Collection[IssueState],
        to_state: IssueState,
        now: float,
        fields: dict[str, Any],
    ) -> tuple[str, list[Any]] | None:
        unknown = set(fields) - self._ISSUE_FIELDS
        if unknown:
            raise ValueError(f"unknown issue field(s): {', '.join(sorted(unknown))}")
        state_values = [state.value for state in from_states]
        if not state_values:
            return None
        assignments = ["state=?", "updated_at=?"]
        values: list[Any] = [to_state.value, now]
        for name, value in fields.items():
            assignments.append(f"{name}=?")
            values.append(value.value if isinstance(value, BumpKind) else value)
        placeholders = ",".join("?" for _ in state_values)
        values.extend([number, *state_values])
        return (
            f"UPDATE issues SET {', '.join(assignments)} WHERE number=? AND state IN ({placeholders})",
            values,
        )

    def transition(
        self,
        number: int,
        from_states: Collection[IssueState],
        to_state: IssueState,
        now: float,
        **fields: Any,
    ) -> bool:
        update = self._transition_update(number, from_states, to_state, now, fields)
        if update is None:
            return False
        sql, values = update
        with self._lock:
            cursor = self._connection.execute(sql, values)
            self._connection.commit()
            return cursor.rowcount == 1

    def settle_and_transition(
        self,
        session_id: str,
        number: int,
        from_states: Collection[IssueState],
        to_state: IssueState,
        now: float,
        *,
        event: tuple[str, str] | None = None,
        **fields: Any,
    ) -> bool:
        update = self._transition_update(number, from_states, to_state, now, fields)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    "UPDATE sessions SET settled_at=?, updated_at=? WHERE session_id=?",
                    (now, now, session_id),
                )
                transitioned = False
                if update is not None:
                    cursor = self._connection.execute(*update)
                    transitioned = cursor.rowcount == 1
                if transitioned and event is not None:
                    self._connection.execute(
                        "INSERT INTO events(issue_number, kind, detail, created_at) VALUES (?, ?, ?, ?)",
                        (number, event[0], event[1], now),
                    )
                self._connection.commit()
                return transitioned
            except Exception:
                self._connection.rollback()
                raise

    def add_event(self, issue_number: int | None, kind: str, detail: str, now: float) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT INTO events(issue_number, kind, detail, created_at) VALUES (?, ?, ?, ?)",
                (issue_number, kind, detail, now),
            )
            self._connection.commit()

    def list_events(self, issue_number: int | None = None, limit: int = 200) -> list[EventRow]:
        with self._lock:
            if issue_number is None:
                rows = self._connection.execute(
                    "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT * FROM events WHERE issue_number=? ORDER BY id DESC LIMIT ?",
                    (issue_number, limit),
                ).fetchall()
        return [_event_row(row) for row in rows]

    def insert_session(self, row: SessionRow) -> None:
        values = _session_values(row)
        with self._lock:
            self._connection.execute(
                "INSERT INTO sessions(session_id, issue_number, stage, status, status_detail, devin_mode, "
                "max_acu_limit, acus_consumed, url, created_at, updated_at, settled_at, archived, nudged, "
                "structured_output) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                values,
            )
            self._connection.commit()

    def update_session(self, session_id: str, **fields: Any) -> None:
        unknown = set(fields) - self._SESSION_FIELDS
        if unknown:
            raise ValueError(f"unknown session field(s): {', '.join(sorted(unknown))}")
        if not fields:
            return
        assignments = []
        values = []
        for name, value in fields.items():
            assignments.append(f"{name}=?")
            if isinstance(value, Stage):
                value = value.value
            elif name in {"archived", "nudged"}:
                value = int(value)
            elif name == "structured_output" and value is not None:
                value = json.dumps(value, separators=(",", ":"))
            values.append(value)
        values.append(session_id)
        with self._lock:
            self._connection.execute(
                f"UPDATE sessions SET {', '.join(assignments)} WHERE session_id=?", values
            )
            self._connection.commit()

    def get_session(self, session_id: str) -> SessionRow | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM sessions WHERE session_id=?", (session_id,)
            ).fetchone()
        return _session_row(row) if row else None

    def list_sessions(
        self,
        stage: Stage | None = None,
        issue_number: int | None = None,
        active_only: bool = False,
    ) -> list[SessionRow]:
        conditions = []
        values: list[Any] = []
        if stage is not None:
            conditions.append("stage=?")
            values.append(stage.value)
        if issue_number is not None:
            conditions.append("issue_number=?")
            values.append(issue_number)
        if active_only:
            conditions.append("settled_at IS NULL")
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM sessions{where} ORDER BY created_at, session_id", values
            ).fetchall()
        return [_session_row(row) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._connection.close()


def _issue_row(row: sqlite3.Row) -> IssueRow:
    bump = row["bump_kind"]
    return IssueRow(
        number=row["number"],
        title=row["title"],
        state=IssueState(row["state"]),
        package=row["package"],
        current_version=row["current_version"],
        fixed_version=row["fixed_version"],
        bump_kind=BumpKind(bump) if bump else None,
        route_reason=row["route_reason"],
        pr_url=row["pr_url"],
        last_error=row["last_error"],
        first_seen_at=row["first_seen_at"],
        updated_at=row["updated_at"],
        triaged_at=row["triaged_at"],
        pr_opened_at=row["pr_opened_at"],
    )


def _event_row(row: sqlite3.Row) -> EventRow:
    return EventRow(
        id=row["id"],
        issue_number=row["issue_number"],
        kind=row["kind"],
        detail=row["detail"],
        created_at=row["created_at"],
    )


def _session_values(row: SessionRow) -> tuple[Any, ...]:
    output = (
        json.dumps(row.structured_output, separators=(",", ":"))
        if row.structured_output is not None
        else None
    )
    return (
        row.session_id,
        row.issue_number,
        row.stage.value,
        row.status,
        row.status_detail,
        row.devin_mode,
        row.max_acu_limit,
        row.acus_consumed,
        row.url,
        row.created_at,
        row.updated_at,
        row.settled_at,
        int(row.archived),
        int(row.nudged),
        output,
    )


def _session_row(row: sqlite3.Row) -> SessionRow:
    structured_output = json.loads(row["structured_output"]) if row["structured_output"] else None
    return SessionRow(
        session_id=row["session_id"],
        issue_number=row["issue_number"],
        stage=Stage(row["stage"]),
        status=row["status"],
        status_detail=row["status_detail"],
        devin_mode=row["devin_mode"],
        max_acu_limit=row["max_acu_limit"],
        acus_consumed=row["acus_consumed"],
        url=row["url"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        settled_at=row["settled_at"],
        archived=bool(row["archived"]),
        nudged=bool(row["nudged"]),
        structured_output=structured_output,
    )
