from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from tests import initialize_source, registry_entries
from warehouse.catalog.bootstrap import initialize_registry
from warehouse.catalog.classification import Classification
from warehouse.catalog.repository import CatalogRepository
from warehouse.catalog.sources import SourceRegistration
from warehouse.facts.amendments import amend_row
from warehouse.facts.batches import post_batch
from warehouse.facts.repository import FactRepository
from warehouse.facts.revisions import read_current_revision


class CatalogTests(unittest.TestCase):
    def test_bootstrap_repeat(self):
        partitions, sources, classifications = registry_entries()
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'source.sqlite3'
            with closing(sqlite3.connect(path)) as connection:
                version = initialize_source(connection)
                self.assertEqual(version, 1)
                catalog = CatalogRepository(connection)
                registry = catalog.load_registry()
                facts = FactRepository(connection)
                receipt = post_batch(connection, 'A', 'meter-A', 'first', [('cooling', 7)])
                row = read_current_revision(catalog, facts, 'A').rows[0]
                correction = amend_row(connection, 'A', row.identity, row, 'cooling', 8)
                reordered = tuple(tuple(reversed(entries)) for entries in (partitions, sources, classifications))
                self.assertEqual(initialize_registry(connection, *reordered), version)
                self.assertEqual(catalog.load_registry(), registry)
                self.assertEqual(post_batch(connection, 'A', 'meter-A', 'first', [('cooling', 7)]), receipt)
                self.assertEqual(read_current_revision(catalog, facts, 'A').rows, (correction.row,))
                self.assertEqual(facts.head_revision('A'), 2)
                conflict = (SourceRegistration('meter-A', 'B'), sources[1])
                with self.assertRaisesRegex(ValueError, '^registry_conflict$'):
                    initialize_registry(connection, partitions, conflict, classifications)
            with closing(sqlite3.connect(path)) as reopened:
                catalog = CatalogRepository(reopened)
                facts = FactRepository(reopened)
                self.assertEqual(catalog.load_registry(), registry)
                self.assertEqual(catalog.load_registry().source_index()['meter-A'].partition, 'A')
                self.assertEqual(read_current_revision(catalog, facts, 'A').rows, (correction.row,))
                self.assertEqual(facts.head_revision('A'), 2)
                self.assertEqual(post_batch(reopened, 'A', 'meter-A', 'after', [('storm', 0)]).revision, 3)

    def test_invalid_registry_is_not_partial(self):
        partitions, sources, classifications = registry_entries()
        with closing(sqlite3.connect(':memory:')) as connection:
            catalog = CatalogRepository(connection)
            with self.assertRaisesRegex(ValueError, '^unknown_partition$'):
                initialize_registry(connection, partitions, sources, classifications + (Classification('C', 'storm'),))
            self.assertIsNone(catalog.load_registry())
            with self.assertRaisesRegex(ValueError, '^duplicate_source$'):
                initialize_registry(connection, partitions, sources + (SourceRegistration('meter-A', 'B'),),
                                    classifications)
            self.assertIsNone(catalog.load_registry())
            self.assertEqual(initialize_source(connection), 1)
