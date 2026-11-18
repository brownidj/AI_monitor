import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import UUID

from .models import JetBrainsSnapshot, Subscription


class Store:
    """Local development storage. Financial decimals remain JSON strings, never SQLite REAL."""

    def __init__(self, path: Path):
        self.path = path

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS records "
                "(id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL)"
            )

    def put(self, kind: str, record: Subscription | JetBrainsSnapshot):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                "INSERT INTO records (id, kind, payload) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET payload = excluded.payload",
                (str(record.id), kind, record.model_dump_json()),
            )

    def list_records(self, kind: str) -> list[str]:
        with closing(sqlite3.connect(self.path)) as connection:
            return [
                row[0]
                for row in connection.execute(
                    "SELECT payload FROM records WHERE kind = ? ORDER BY rowid", (kind,)
                )
            ]

    def subscriptions(self) -> list[Subscription]:
        return [Subscription.model_validate_json(row) for row in self.list_records("subscription")]

    def snapshots(self) -> list[JetBrainsSnapshot]:
        return sorted(
            [JetBrainsSnapshot.model_validate_json(row) for row in self.list_records("jetbrains")],
            key=lambda snapshot: snapshot.observed_at,
            reverse=True,
        )

    def delete(self, kind: str, record_id: UUID) -> bool:
        with closing(sqlite3.connect(self.path)) as connection, connection:
            result = connection.execute(
                "DELETE FROM records WHERE kind = ? AND id = ?", (kind, str(record_id))
            )
            return result.rowcount > 0
