from contextlib import contextmanager
from dataclasses import asdict, dataclass
import json
import sqlite3

from warehouse.facts.rows import RowIdentity


@dataclass(frozen=True)
class BatchEvent:
    revision: int
    source: str
    batch: str
    readings: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class AmendmentEvent:
    revision: int
    identity: RowIdentity
    category: str
    liters: int


class FactRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        connection.execute('''CREATE TABLE IF NOT EXISTS fact_event (
            partition TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK (revision > 0),
            batch_source TEXT,
            batch_no TEXT,
            payload TEXT NOT NULL,
            PRIMARY KEY (partition, revision),
            UNIQUE (partition, batch_source, batch_no),
            CHECK ((batch_source IS NULL) = (batch_no IS NULL))
        )''')

    @contextmanager
    def transaction(self):
        self.connection.execute('BEGIN IMMEDIATE')
        with self.connection:
            yield

    def find_batch(self, partition: str, source: str, batch: str) -> BatchEvent | None:
        record = self.connection.execute('''SELECT revision, batch_source, batch_no, payload
            FROM fact_event WHERE partition = ? AND batch_source = ? AND batch_no = ?''',
            (partition, source, batch)).fetchone()
        return self._decode_event(record) if record is not None else None

    def append_batch(self, partition, source, batch, revision, rows) -> None:
        readings = [(row.category, row.liters) for row in rows]
        self.connection.execute('INSERT INTO fact_event VALUES (?, ?, ?, ?, ?)',
                                (partition, revision, source, batch, json.dumps(readings)))

    def append_amendment(self, partition, revision, row) -> None:
        payload = {'identity': asdict(row.identity), 'category': row.category, 'liters': row.liters}
        self.connection.execute('INSERT INTO fact_event VALUES (?, ?, NULL, NULL, ?)',
                                (partition, revision, json.dumps(payload)))

    def head_revision(self, partition: str) -> int:
        return self.connection.execute('SELECT COALESCE(MAX(revision), 0) FROM fact_event WHERE partition = ?',
                                       (partition,)).fetchone()[0]

    def read_revision_events(self, partition: str, revision: int) -> tuple[BatchEvent | AmendmentEvent, ...]:
        records = self.connection.execute('''SELECT revision, batch_source, batch_no, payload FROM fact_event
            WHERE partition = ? AND revision <= ? ORDER BY revision''', (partition, revision))
        return tuple(self._decode_event(record) for record in records)

    @staticmethod
    def _decode_event(record) -> BatchEvent | AmendmentEvent:
        revision, source, batch, payload = record
        data = json.loads(payload)
        if source is not None:
            return BatchEvent(revision, source, batch, tuple(tuple(reading) for reading in data))
        return AmendmentEvent(revision, RowIdentity(**data['identity']), data['category'], data['liters'])
