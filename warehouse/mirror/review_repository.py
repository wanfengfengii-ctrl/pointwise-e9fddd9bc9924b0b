from contextlib import contextmanager
from collections.abc import Callable
from dataclasses import dataclass
import sqlite3

from warehouse.release.review_format import BasisBinding


@dataclass(frozen=True)
class ReviewReceipt:
    release_id: str
    partition: str
    revision: int
    basis_id: str


@dataclass(frozen=True)
class StoredReviewRelease:
    receipt: ReviewReceipt
    artifact: bytes
    ledger_sha256: str


class MirrorReviewRepository:
    def __init__(self, connection: sqlite3.Connection, *, before_commit: Callable[[], None] | None = None):
        self.connection = connection
        self.before_commit = before_commit
        connection.execute('''CREATE TABLE IF NOT EXISTS review_received (
            release_id TEXT PRIMARY KEY NOT NULL,
            partition TEXT NOT NULL,
            revision INTEGER NOT NULL,
            basis_id TEXT NOT NULL,
            ledger_sha256 TEXT NOT NULL,
            artifact BLOB NOT NULL
        )''')
        connection.execute('''CREATE INDEX IF NOT EXISTS review_received_revision
            ON review_received (partition, revision)''')
        connection.execute('''CREATE TABLE IF NOT EXISTS review_basis_received (
            basis_id TEXT PRIMARY KEY NOT NULL,
            partition TEXT NOT NULL,
            revision INTEGER NOT NULL,
            ledger_sha256 TEXT NOT NULL,
            parent_basis_id TEXT
        )''')
        connection.execute('''CREATE TABLE IF NOT EXISTS review_revocation_received (
            notice_id TEXT PRIMARY KEY NOT NULL,
            basis_id TEXT NOT NULL,
            artifact BLOB NOT NULL
        )''')

    @contextmanager
    def transaction(self):
        self.connection.execute('BEGIN IMMEDIATE')
        with self.connection:
            yield
            if self.before_commit is not None:
                self.before_commit()

    def find_release(self, release_id: str) -> StoredReviewRelease | None:
        record = self.connection.execute('''SELECT release_id, partition, revision, basis_id,
            ledger_sha256, artifact FROM review_received WHERE release_id = ?''',
            (release_id,)).fetchone()
        if record is None:
            return None
        release_id, partition, revision, basis_id, ledger_sha256, artifact = record
        return StoredReviewRelease(ReviewReceipt(release_id, partition, revision, basis_id), artifact, ledger_sha256)

    def list_releases(self, partition: str) -> tuple[StoredReviewRelease, ...]:
        records = self.connection.execute('''SELECT release_id, partition, revision, basis_id,
            ledger_sha256, artifact FROM review_received WHERE partition = ?
            ORDER BY revision DESC, release_id ASC''', (partition,)).fetchall()
        return tuple(
            StoredReviewRelease(ReviewReceipt(r[0], r[1], r[2], r[3]), r[5], r[4]) for r in records
        )

    def find_revision_ledger(self, partition: str, revision: int) -> str | None:
        record = self.connection.execute('''SELECT ledger_sha256 FROM review_received
            WHERE partition = ? AND revision = ? LIMIT 1''', (partition, revision)).fetchone()
        return record[0] if record is not None else None

    def insert_release(self, receipt: ReviewReceipt, artifact: bytes, ledger_sha256: str) -> None:
        self.connection.execute('INSERT INTO review_received VALUES (?, ?, ?, ?, ?, ?)', (
            receipt.release_id, receipt.partition, receipt.revision,
            receipt.basis_id, ledger_sha256, artifact,
        ))

    def find_basis(self, basis_id: str) -> BasisBinding | None:
        record = self.connection.execute('''SELECT basis_id, partition, revision, ledger_sha256, parent_basis_id
            FROM review_basis_received WHERE basis_id = ?''', (basis_id,)).fetchone()
        return BasisBinding(*record) if record is not None else None

    def list_children(self, parent_basis_id: str) -> tuple[BasisBinding, ...]:
        records = self.connection.execute('''SELECT basis_id, partition, revision, ledger_sha256, parent_basis_id
            FROM review_basis_received WHERE parent_basis_id = ?''', (parent_basis_id,)).fetchall()
        return tuple(BasisBinding(*record) for record in records)

    def insert_basis(self, basis: BasisBinding) -> None:
        self.connection.execute('INSERT INTO review_basis_received VALUES (?, ?, ?, ?, ?)', (
            basis.basis_id, basis.partition, basis.revision, basis.ledger_sha256, basis.parent_basis_id,
        ))

    def find_revocation(self, notice_id: str):
        record = self.connection.execute('''SELECT basis_id, artifact
            FROM review_revocation_received WHERE notice_id = ?''', (notice_id,)).fetchone()
        return record

    def has_revocation_for_basis(self, basis_id: str) -> bool:
        record = self.connection.execute('''SELECT 1 FROM review_revocation_received
            WHERE basis_id = ? LIMIT 1''', (basis_id,)).fetchone()
        return record is not None

    def insert_revocation(self, notice_id: str, basis_id: str, artifact: bytes) -> None:
        self.connection.execute('INSERT INTO review_revocation_received VALUES (?, ?, ?)',
                                (notice_id, basis_id, artifact))
