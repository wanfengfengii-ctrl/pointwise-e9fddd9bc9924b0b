from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from tests import initialize_source
from warehouse.catalog.repository import CatalogRepository
from warehouse.close.freeze import freeze_partition
from warehouse.close.repository import CloseRepository
from warehouse.close.tally import CategoryTotal, LedgerTally
from warehouse.facts.amendments import amend_row
from warehouse.facts.batches import post_batch
from warehouse.facts.repository import FactRepository
from warehouse.facts.revisions import read_current_revision
from warehouse.facts.rows import RowProvenance
from warehouse.release.format import decode_v1
from warehouse.release.issue import issue_release
from warehouse.release.repository import ReleaseRepository


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        connection = sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        self.connection = connection
        initialize_source(connection)

    def post_original(self):
        receipt = post_batch(self.connection, 'A', 'meter-A', 'original', [('cooling', 7), ('cooling', 0)])
        view = read_current_revision(CatalogRepository(self.connection), FactRepository(self.connection), 'A')
        return receipt, view.rows[0]

    def test_frozen_issue(self):
        receipt, old_row = self.post_original()
        frozen = freeze_partition(self.connection, 'fixed-1', 'A', receipt.revision)
        first = issue_release(self.connection, 'issue-1', 'fixed-1')
        amend_row(self.connection, 'A', old_row.identity, old_row, 'cooling', 8)
        self.assertEqual(freeze_partition(self.connection, 'fixed-1', 'A', 1), frozen)
        self.assertEqual(issue_release(self.connection, 'issue-1', 'fixed-1'), first)
        issue, artifact = first
        manifest, ledger = decode_v1(artifact)
        self.assertEqual((manifest.release_id, manifest.snapshot_id, manifest.partition, manifest.revision),
                         ('issue-1', 'fixed-1', 'A', 1))
        self.assertEqual(issue.ledger_sha256, manifest.ledger_sha256)
        self.assertEqual(ledger.tally, LedgerTally(2, 7, (CategoryTotal('cooling', 2, 7),)))
        self.assertEqual([row.liters for row in ledger.rows], [7, 0])
        self.assertEqual([row.provenance for row in ledger.rows],
                         [RowProvenance('A', 'meter-A', 'original', 1, 1),
                          RowProvenance('A', 'meter-A', 'original', 2, 1)])

    def test_historical_freeze_after_amend(self):
        _, old_row = self.post_original()
        amend_row(self.connection, 'A', old_row.identity, old_row, 'storm', 8)
        historical = freeze_partition(self.connection, 'old', 'A', 1)
        current = freeze_partition(self.connection, 'new', 'A', 2)
        self.assertEqual((historical.revision, current.revision), (1, 2))
        self.assertEqual([row.liters for row in historical.rows], [7, 0])
        self.assertEqual([row.liters for row in current.rows], [8, 0])
        self.assertEqual(current.tally, LedgerTally(2, 8, (CategoryTotal('cooling', 1, 0), CategoryTotal('storm', 1, 8))))
        self.assertEqual(historical.tally, LedgerTally(2, 7, (CategoryTotal('cooling', 2, 7),)))
        self.assertEqual(current.rows[0].provenance, old_row.provenance)

    def test_empty_revision_and_unknown_revision(self):
        self.post_original()
        empty = freeze_partition(self.connection, 'empty', 'A', 0)
        self.assertEqual((empty.revision, empty.rows, empty.tally), (0, (), LedgerTally(0, 0, ())))
        _, data = issue_release(self.connection, 'empty-issue', 'empty')
        manifest, ledger = decode_v1(data)
        self.assertEqual((manifest.revision, ledger.rows, ledger.tally), (0, (), LedgerTally(0, 0, ())))
        for revision in (2, -1, True):
            with self.subTest(revision=revision):
                with self.assertRaisesRegex(ValueError, '^unknown_revision$'):
                    freeze_partition(self.connection, 'unused', 'A', revision)
        with self.assertRaisesRegex(ValueError, '^unknown_partition$'):
            freeze_partition(self.connection, 'unused', 'C', 0)
        self.assertIsNone(CloseRepository(self.connection).find_snapshot('unused'))
        self.assertEqual(freeze_partition(self.connection, 'unused', 'B', 0).rows, ())

    def test_snapshot_and_issue_binding_conflicts(self):
        _, old = self.post_original()
        frozen = freeze_partition(self.connection, 'fixed-1', 'A', 1)
        original_issue = issue_release(self.connection, 'issue-1', 'fixed-1')
        amend_row(self.connection, 'A', old.identity, old, 'cooling', 8)
        freeze_partition(self.connection, 'fixed-2', 'A', 2)
        with self.assertRaisesRegex(ValueError, '^snapshot_conflict$'):
            freeze_partition(self.connection, 'fixed-1', 'A', 2)
        self.assertEqual(freeze_partition(self.connection, 'fixed-1', 'A', 1), frozen)
        with self.assertRaisesRegex(ValueError, '^issue_binding_conflict$'):
            issue_release(self.connection, 'issue-1', 'fixed-2')
        self.assertEqual(issue_release(self.connection, 'issue-1', 'fixed-1'), original_issue)
        with self.assertRaisesRegex(ValueError, '^unknown_snapshot$'):
            issue_release(self.connection, 'unused', 'missing')
        self.assertIsNone(ReleaseRepository(self.connection).find_release('unused'))
        self.assertEqual(issue_release(self.connection, 'unused', 'fixed-2')[0].revision, 2)

    def test_release_reopens_and_reproduces_bytes(self):
        with TemporaryDirectory() as directory:
            first_path = Path(directory) / 'first.sqlite3'
            second_path = Path(directory) / 'second.sqlite3'
            issued = []
            for path in (first_path, second_path):
                with closing(sqlite3.connect(path)) as connection:
                    initialize_source(connection)
                    post_batch(connection, 'A', 'meter-A', 'original', [('cooling', 7), ('storm', 0)])
                    freeze_partition(connection, 'fixed-1', 'A', 1)
                    issued.append(issue_release(connection, 'issue-1', 'fixed-1'))
            self.assertEqual(issued[0], issued[1])
            with closing(sqlite3.connect(first_path)) as connection:
                self.assertEqual(issue_release(connection, 'issue-1', 'fixed-1'), issued[0])
                self.assertEqual(freeze_partition(connection, 'fixed-1', 'A', 1).tally.liters, 7)
