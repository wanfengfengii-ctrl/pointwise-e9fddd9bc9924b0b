from contextlib import contextmanager
from dataclasses import dataclass
import sqlite3

from warehouse.catalog.classification import Classification
from warehouse.catalog.partitions import PartitionDefinition
from warehouse.catalog.sources import SourceRegistration


@dataclass(frozen=True)
class Registry:
    version: int
    partitions: tuple[PartitionDefinition, ...]
    sources: tuple[SourceRegistration, ...]
    classifications: tuple[Classification, ...]

    def partition_index(self):
        return {entry.name: entry for entry in self.partitions}

    def source_index(self):
        return {entry.source: entry for entry in self.sources}

    def classification_index(self):
        return {(entry.partition, entry.category): entry for entry in self.classifications}


class CatalogRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        connection.execute('''CREATE TABLE IF NOT EXISTS registry_version (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1), version INTEGER NOT NULL
        )''')
        connection.execute('''CREATE TABLE IF NOT EXISTS registry_partition (
            name TEXT PRIMARY KEY NOT NULL
        )''')
        connection.execute('''CREATE TABLE IF NOT EXISTS registry_source (
            source TEXT PRIMARY KEY NOT NULL, partition TEXT NOT NULL
        )''')
        connection.execute('''CREATE TABLE IF NOT EXISTS registry_classification (
            partition TEXT NOT NULL, category TEXT NOT NULL, PRIMARY KEY (partition, category)
        )''')

    @contextmanager
    def transaction(self):
        self.connection.execute('BEGIN IMMEDIATE')
        with self.connection:
            yield

    def load_registry(self) -> Registry | None:
        version = self.connection.execute('SELECT version FROM registry_version WHERE singleton = 1').fetchone()
        if version is None:
            return None
        partitions = self.connection.execute('SELECT name FROM registry_partition ORDER BY name')
        sources = self.connection.execute('SELECT source, partition FROM registry_source ORDER BY source')
        categories = self.connection.execute('''SELECT partition, category FROM registry_classification
                                                  ORDER BY partition, category''')
        return Registry(
            version[0],
            tuple(PartitionDefinition(*item) for item in partitions),
            tuple(SourceRegistration(*item) for item in sources),
            tuple(Classification(*item) for item in categories),
        )

    def insert_registry(self, registry: Registry) -> None:
        self.connection.execute('INSERT INTO registry_version VALUES (1, ?)', (registry.version,))
        self.connection.executemany('INSERT INTO registry_partition VALUES (?)',
                                    ((item.name,) for item in registry.partitions))
        self.connection.executemany('INSERT INTO registry_source VALUES (?, ?)',
                                    ((item.source, item.partition) for item in registry.sources))
        self.connection.executemany('INSERT INTO registry_classification VALUES (?, ?)',
                                    ((item.partition, item.category) for item in registry.classifications))


def require_registry(catalog: CatalogRepository) -> Registry:
    registry = catalog.load_registry()
    if registry is None:
        raise ValueError('uninitialized_registry')
    return registry
