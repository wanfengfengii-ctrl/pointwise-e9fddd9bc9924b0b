from contextlib import contextmanager
from dataclasses import dataclass
import sqlite3


@dataclass(frozen=True)
class StoredRelease:
    snapshot_id: str
    partition: str
    revision: int
    ledger_sha256: str
    artifact: bytes


class ReleaseRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        connection.execute('''CREATE TABLE IF NOT EXISTS issued_release (
            release_id TEXT PRIMARY KEY NOT NULL,
            snapshot_id TEXT NOT NULL,
            partition TEXT NOT NULL,
            revision INTEGER NOT NULL,
            ledger_sha256 TEXT NOT NULL,
            artifact BLOB NOT NULL
        )''')

    @contextmanager
    def transaction(self):
        self.connection.execute('BEGIN IMMEDIATE')
        with self.connection:
            yield

    def find_release(self, release_id: str) -> StoredRelease | None:
        record = self.connection.execute('''SELECT snapshot_id, partition, revision, ledger_sha256, artifact
            FROM issued_release WHERE release_id = ?''', (release_id,)).fetchone()
        return StoredRelease(*record) if record is not None else None

    def insert_release(self, release_id: str, release: StoredRelease) -> None:
        self.connection.execute('INSERT INTO issued_release VALUES (?, ?, ?, ?, ?, ?)', (
            release_id, release.snapshot_id, release.partition, release.revision, release.ledger_sha256, release.artifact,
        ))
