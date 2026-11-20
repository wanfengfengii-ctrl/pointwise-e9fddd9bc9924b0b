from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
import sqlite3


@dataclass(frozen=True)
class Receipt:
    release_id: str
    partition: str
    revision: int


@dataclass(frozen=True)
class ReceivedArtifact:
    receipt: Receipt
    artifact: bytes


class MirrorRepository:
    def __init__(self, connection: sqlite3.Connection, *, before_commit: Callable[[], None] | None = None):
        self.connection = connection
        self.before_commit = before_commit
        connection.execute('''CREATE TABLE IF NOT EXISTS mirror_received (
            release_id TEXT PRIMARY KEY NOT NULL,
            partition TEXT NOT NULL,
            revision INTEGER NOT NULL,
            artifact BLOB NOT NULL,
            canonical_ledger BLOB NOT NULL
        )''')
        connection.execute('''CREATE INDEX IF NOT EXISTS mirror_received_revision
            ON mirror_received (partition, revision)''')
        connection.execute('''CREATE TABLE IF NOT EXISTS mirror_current (
            partition TEXT PRIMARY KEY NOT NULL,
            release_id TEXT NOT NULL REFERENCES mirror_received (release_id)
        )''')

    @contextmanager
    def transaction(self):
        self.connection.execute('BEGIN IMMEDIATE')
        with self.connection:
            yield
            if self.before_commit is not None:
                self.before_commit()

    def find_receipt(self, release_id: str) -> ReceivedArtifact | None:
        record = self.connection.execute('''SELECT release_id, partition, revision, artifact
            FROM mirror_received WHERE release_id = ?''', (release_id,)).fetchone()
        return self._received(record)

    def find_revision_ledger(self, partition: str, revision: int) -> bytes | None:
        record = self.connection.execute('''SELECT canonical_ledger FROM mirror_received
            WHERE partition = ? AND revision = ? LIMIT 1''', (partition, revision)).fetchone()
        return record[0] if record is not None else None

    def get_current(self, partition: str) -> ReceivedArtifact | None:
        record = self.connection.execute('''SELECT r.release_id, r.partition, r.revision, r.artifact
            FROM mirror_current AS c JOIN mirror_received AS r ON r.release_id = c.release_id
            WHERE c.partition = ?''', (partition,)).fetchone()
        return self._received(record)

    def insert_receipt(self, receipt: Receipt, artifact: bytes, canonical_ledger: bytes) -> None:
        self.connection.execute('INSERT INTO mirror_received VALUES (?, ?, ?, ?, ?)',
                                (receipt.release_id, receipt.partition, receipt.revision, artifact, canonical_ledger))

    def set_current(self, receipt: Receipt) -> None:
        self.connection.execute('''INSERT INTO mirror_current (partition, release_id) VALUES (?, ?)
            ON CONFLICT (partition) DO UPDATE SET release_id = excluded.release_id''',
            (receipt.partition, receipt.release_id))

    @staticmethod
    def _received(record) -> ReceivedArtifact | None:
        if record is None:
            return None
        release_id, partition, revision, artifact = record
        return ReceivedArtifact(Receipt(release_id, partition, revision), artifact)
