from contextlib import contextmanager
from dataclasses import asdict, dataclass
import json
import sqlite3

from warehouse.close.tally import LedgerTally, tally_from_data
from warehouse.facts.rows import MeasurementRow, row_from_data


@dataclass(frozen=True)
class FrozenSnapshot:
    snapshot_id: str
    partition: str
    revision: int
    rows: tuple[MeasurementRow, ...]
    tally: LedgerTally


class CloseRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        connection.execute('''CREATE TABLE IF NOT EXISTS frozen_snapshot (
            snapshot_id TEXT PRIMARY KEY NOT NULL,
            partition TEXT NOT NULL,
            revision INTEGER NOT NULL,
            rows_json TEXT NOT NULL,
            tally_json TEXT NOT NULL
        )''')

    @contextmanager
    def transaction(self):
        self.connection.execute('BEGIN IMMEDIATE')
        with self.connection:
            yield

    def find_snapshot(self, snapshot_id: str) -> FrozenSnapshot | None:
        record = self.connection.execute('''SELECT partition, revision, rows_json, tally_json
            FROM frozen_snapshot WHERE snapshot_id = ?''', (snapshot_id,)).fetchone()
        if record is None:
            return None
        partition, revision, rows_json, tally_json = record
        rows = tuple(row_from_data(row) for row in json.loads(rows_json))
        return FrozenSnapshot(snapshot_id, partition, revision, rows, tally_from_data(json.loads(tally_json)))

    def insert_snapshot(self, snapshot: FrozenSnapshot) -> None:
        rows_json = json.dumps([asdict(row) for row in snapshot.rows], ensure_ascii=False)
        tally_json = json.dumps(asdict(snapshot.tally), ensure_ascii=False)
        self.connection.execute('INSERT INTO frozen_snapshot VALUES (?, ?, ?, ?, ?)',
                                (snapshot.snapshot_id, snapshot.partition, snapshot.revision, rows_json, tally_json))
