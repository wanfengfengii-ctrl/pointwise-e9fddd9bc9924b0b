from contextlib import closing
from dataclasses import replace
import sqlite3
import unittest

from tests import initialize_source
from warehouse.catalog.repository import CatalogRepository
from warehouse.facts.amendments import amend_row
from warehouse.facts.batches import post_batch
from warehouse.facts.repository import FactRepository
from warehouse.facts.revisions import read_current_revision, read_fact_revision
from warehouse.facts.rows import RowIdentity, RowProvenance


class FactTests(unittest.TestCase):
    def setUp(self):
        connection = sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        self.connection = connection
        initialize_source(connection)
        self.catalog = CatalogRepository(connection)
        self.facts = FactRepository(connection)

    def current(self, partition='A'):
        return read_current_revision(self.catalog, self.facts, partition)

    def test_batch_replay(self):
        readings = [('cooling', 7), ('cooling', 7)]
        receipt = post_batch(self.connection, 'A', 'meter-A', 'first', readings)
        self.assertEqual(post_batch(self.connection, 'A', 'meter-A', 'first', readings), receipt)
        self.assertEqual((receipt.revision, receipt.row_count), (1, 2))
        view = self.current()
        self.assertEqual(view.revision, 1)
        self.assertEqual([row.liters for row in view.rows], [7, 7])
        self.assertEqual(sum(row.liters for row in view.rows), 14)
        self.assertEqual([row.provenance for row in view.rows],
                         [RowProvenance('A', 'meter-A', 'first', 1, 1), RowProvenance('A', 'meter-A', 'first', 2, 1)])

    def test_changed_payload_and_replay_after_amend(self):
        readings = [('cooling', 7), ('cooling', 0)]
        receipt = post_batch(self.connection, 'A', 'meter-A', 'first', readings)
        original = self.current()
        with self.assertRaisesRegex(ValueError, '^batch_conflict$'):
            post_batch(self.connection, 'A', 'meter-A', 'first', list(reversed(readings)))
        self.assertEqual(self.current(), original)
        old = original.rows[0]
        amend_row(self.connection, 'A', old.identity, old, 'storm', 8)
        self.assertEqual(post_batch(self.connection, 'A', 'meter-A', 'first', readings), receipt)
        with self.assertRaisesRegex(ValueError, '^batch_conflict$'):
            post_batch(self.connection, 'A', 'meter-A', 'first', [('storm', 8), ('cooling', 0)])
        view = self.current()
        self.assertEqual(view.revision, 2)
        self.assertEqual([(row.category, row.liters) for row in view.rows], [('storm', 8), ('cooling', 0)])
        self.assertEqual(view.rows[0].provenance, old.provenance)
        self.assertEqual(read_fact_revision(self.catalog, self.facts, 'A', 1), original)

    def test_invalid_category(self):
        post_batch(self.connection, 'A', 'meter-A', 'first', [('cooling', 2)])
        before = self.current()
        with self.assertRaisesRegex(ValueError, '^unknown_partition_category$'):
            post_batch(self.connection, 'A', 'meter-A', 'second', [('cooling', 1), ('effluent', 4)])
        self.assertIsNone(self.facts.find_batch('A', 'meter-A', 'second'))
        self.assertEqual(self.current(), before)
        self.assertEqual(self.current('B').revision, 0)
        self.assertEqual(post_batch(self.connection, 'A', 'meter-A', 'second', [('storm', 4)]).revision, 2)

    def test_invalid_measurements_and_source_do_not_advance(self):
        for value in (None, True, 1.0, '1'):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, '^invalid_liters$'):
                    post_batch(self.connection, 'A', 'meter-A', 'invalid', [('cooling', 0), ('storm', value)])
                self.assertIsNone(self.facts.find_batch('A', 'meter-A', 'invalid'))
        with self.assertRaisesRegex(ValueError, '^source_partition_mismatch$'):
            post_batch(self.connection, 'A', 'meter-B', 'invalid', [('cooling', 1)])
        self.assertEqual((self.current().revision, self.current().rows), (0, ()))

    def test_amend_stale(self):
        post_batch(self.connection, 'A', 'meter-A', 'first', [('cooling', 7)])
        old = self.current().rows[0]
        receipt = amend_row(self.connection, 'A', old.identity, old, 'cooling', 8)
        with self.assertRaisesRegex(ValueError, '^stale_old_image$'):
            amend_row(self.connection, 'A', old.identity, old, 'cooling', 9)
        self.assertEqual((receipt.revision, receipt.row.liters), (2, 8))
        self.assertEqual(receipt.row.provenance, old.provenance)
        self.assertEqual((self.current().revision, self.current().rows), (2, (receipt.row,)))

    def test_full_old_image_and_unknown_row(self):
        post_batch(self.connection, 'A', 'meter-A', 'first', [('cooling', 7), ('cooling', 7)])
        before = self.current()
        first, second = before.rows
        wrong_images = (replace(first, provenance=second.provenance), replace(first, category='storm'))
        for wrong_image in wrong_images:
            with self.subTest(wrong_image=wrong_image):
                with self.assertRaisesRegex(ValueError, '^stale_old_image$'):
                    amend_row(self.connection, 'A', first.identity, wrong_image, 'cooling', 8)
        missing = RowIdentity('A', 'meter-A', 'first', 3)
        with self.assertRaisesRegex(ValueError, '^unknown_row$'):
            amend_row(self.connection, 'A', missing, first, 'cooling', 8)
        with self.assertRaisesRegex(ValueError, '^unknown_partition_category$'):
            amend_row(self.connection, 'A', first.identity, first, 'effluent', 8)
        self.assertEqual(self.current(), before)
        amend_row(self.connection, 'A', second.identity, second, 'cooling', 8)
        self.assertEqual([row.liters for row in self.current().rows], [7, 8])
        self.assertEqual([row.provenance for row in self.current().rows], [first.provenance, second.provenance])

    def test_integer_liters_preserved_above_sqlite_integer_range(self):
        with closing(sqlite3.connect(':memory:')) as connection:
            initialize_source(connection)
            reading = 2**70 + 3
            post_batch(connection, 'B', 'meter-B', 'wide', [('effluent', reading)])
            view = read_current_revision(CatalogRepository(connection), FactRepository(connection), 'B')
            self.assertEqual(view.rows[0].liters, reading)
