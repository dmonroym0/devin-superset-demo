"""Atomic global ACU reservation ledger."""

from __future__ import annotations

from app.db import Database
from app.models import Stage


class Budget:
    def __init__(self, db: Database, ceiling: int):
        if ceiling <= 0:
            raise ValueError("ceiling must be positive")
        self._db = db
        self._ceiling = ceiling

    @property
    def ceiling(self) -> int:
        return self._ceiling

    def reserve(self, issue_number: int, stage: Stage, cap: int, now: float) -> int | None:
        if cap <= 0:
            raise ValueError("cap must be positive")
        with self._db._lock:
            connection = self._db._connection
            connection.execute("BEGIN IMMEDIATE")
            try:
                committed = connection.execute(
                    "SELECT COALESCE(SUM(cap), 0) FROM ledger WHERE cancelled=0"
                ).fetchone()[0]
                if committed + cap > self._ceiling:
                    connection.rollback()
                    return None
                cursor = connection.execute(
                    "INSERT INTO ledger(issue_number, stage, cap, created_at, cancelled) "
                    "VALUES (?, ?, ?, ?, 0)",
                    (issue_number, stage.value, cap, now),
                )
                reservation_id = cursor.lastrowid
                connection.commit()
                return int(reservation_id)
            except Exception:
                connection.rollback()
                raise

    def attach(self, reservation_id: int, session_id: str) -> bool:
        with self._db._lock:
            cursor = self._db._connection.execute(
                "UPDATE ledger SET session_id=? WHERE id=? AND cancelled=0",
                (session_id, reservation_id),
            )
            self._db._connection.commit()
            return cursor.rowcount == 1

    def cancel(self, reservation_id: int) -> bool:
        with self._db._lock:
            cursor = self._db._connection.execute(
                "UPDATE ledger SET cancelled=1 WHERE id=? AND cancelled=0", (reservation_id,)
            )
            self._db._connection.commit()
            return cursor.rowcount == 1

    def mark_create_started(self, reservation_id: int, now: float) -> bool:
        with self._db._lock:
            cursor = self._db._connection.execute(
                "UPDATE ledger SET create_started_at=? "
                "WHERE id=? AND cancelled=0 AND session_id IS NULL",
                (now, reservation_id),
            )
            self._db._connection.commit()
            return cursor.rowcount == 1

    def cancel_unattached(self, issue_number: int, stage: Stage) -> int:
        with self._db._lock:
            cursor = self._db._connection.execute(
                "UPDATE ledger SET cancelled=1 "
                "WHERE issue_number=? AND stage=? AND session_id IS NULL AND cancelled=0 "
                "AND create_started_at IS NULL",
                (issue_number, stage.value),
            )
            self._db._connection.commit()
            return cursor.rowcount

    def unattached_create_started(self, issue_number: int, stage: Stage) -> int:
        with self._db._lock:
            row = self._db._connection.execute(
                "SELECT COUNT(*) FROM ledger WHERE issue_number=? AND stage=? "
                "AND session_id IS NULL AND cancelled=0 AND create_started_at IS NOT NULL",
                (issue_number, stage.value),
            ).fetchone()
        return int(row[0])

    def attached_without_session(self, issue_number: int, stage: Stage) -> list[tuple[int, str, int, float]]:
        with self._db._lock:
            rows = self._db._connection.execute(
                "SELECT ledger.id, ledger.session_id, ledger.cap, ledger.created_at "
                "FROM ledger WHERE ledger.issue_number=? AND ledger.stage=? AND ledger.cancelled=0 "
                "AND ledger.session_id IS NOT NULL "
                "AND NOT EXISTS (SELECT 1 FROM sessions WHERE sessions.session_id=ledger.session_id) "
                "ORDER BY ledger.id",
                (issue_number, stage.value),
            ).fetchall()
        return [
            (int(row["id"]), str(row["session_id"]), int(row["cap"]), float(row["created_at"]))
            for row in rows
        ]

    def committed(self) -> int:
        with self._db._lock:
            row = self._db._connection.execute(
                "SELECT COALESCE(SUM(cap), 0) FROM ledger WHERE cancelled=0"
            ).fetchone()
        return int(row[0])

    def remaining(self) -> int:
        return self._ceiling - self.committed()
