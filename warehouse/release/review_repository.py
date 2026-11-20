from contextlib import contextmanager
import sqlite3

from warehouse.release.review_format import BasisBinding


class ReviewSourceRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        connection.execute('''CREATE TABLE IF NOT EXISTS review_basis (
            basis_id TEXT PRIMARY KEY NOT NULL,
            partition TEXT NOT NULL,
            revision INTEGER NOT NULL,
            ledger_sha256 TEXT NOT NULL,
            parent_basis_id TEXT
        )''')
        connection.execute('''CREATE TABLE IF NOT EXISTS review_release (
            release_id TEXT PRIMARY KEY NOT NULL,
            snapshot_id TEXT NOT NULL,
            basis_id TEXT NOT NULL,
            artifact BLOB NOT NULL
        )''')
        connection.execute('''CREATE TABLE IF NOT EXISTS review_revocation (
            notice_id TEXT PRIMARY KEY NOT NULL,
            basis_id TEXT NOT NULL,
            artifact BLOB NOT NULL
        )''')
        connection.execute('''CREATE TABLE IF NOT EXISTS review_basis_export (
            basis_id TEXT PRIMARY KEY NOT NULL,
            artifact BLOB NOT NULL
        )''')

    @contextmanager
    def transaction(self):
        self.connection.execute('BEGIN IMMEDIATE')
        with self.connection:
            yield

    def find_basis(self, basis_id: str) -> BasisBinding | None:
        record = self.connection.execute('''SELECT basis_id, partition, revision, ledger_sha256, parent_basis_id
            FROM review_basis WHERE basis_id = ?''', (basis_id,)).fetchone()
        return BasisBinding(*record) if record is not None else None

    def insert_basis(self, basis: BasisBinding) -> None:
        self.connection.execute('INSERT INTO review_basis VALUES (?, ?, ?, ?, ?)', (
            basis.basis_id, basis.partition, basis.revision, basis.ledger_sha256, basis.parent_basis_id,
        ))

    def find_release(self, release_id: str):
        record = self.connection.execute('''SELECT snapshot_id, basis_id, artifact
            FROM review_release WHERE release_id = ?''', (release_id,)).fetchone()
        return record

    def insert_release(self, release_id: str, snapshot_id: str, basis_id: str, artifact: bytes) -> None:
        self.connection.execute('INSERT INTO review_release VALUES (?, ?, ?, ?)',
                                (release_id, snapshot_id, basis_id, artifact))

    def find_revocation(self, notice_id: str):
        record = self.connection.execute('''SELECT basis_id, artifact
            FROM review_revocation WHERE notice_id = ?''', (notice_id,)).fetchone()
        return record

    def insert_revocation(self, notice_id: str, basis_id: str, artifact: bytes) -> None:
        self.connection.execute('INSERT INTO review_revocation VALUES (?, ?, ?)',
                                (notice_id, basis_id, artifact))

    def find_basis_export(self, basis_id: str) -> bytes | None:
        record = self.connection.execute('''SELECT artifact FROM review_basis_export WHERE basis_id = ?''',
                                         (basis_id,)).fetchone()
        return record[0] if record is not None else None

    def insert_basis_export(self, basis_id: str, artifact: bytes) -> None:
        self.connection.execute('INSERT INTO review_basis_export VALUES (?, ?)', (basis_id, artifact))
