from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from tests import initialize_source
from warehouse.catalog.repository import CatalogRepository
from warehouse.close.freeze import freeze_partition
from warehouse.close.tally import CategoryTotal, LedgerTally
from warehouse.facts.amendments import amend_row
from warehouse.facts.batches import post_batch
from warehouse.facts.repository import FactRepository
from warehouse.facts.revisions import read_current_revision
from warehouse.facts.rows import RowProvenance
from warehouse.mirror.admission import install_release
from warehouse.mirror.read import audit_partition, audit_release
from warehouse.mirror.repository import MirrorRepository, Receipt
from warehouse.release.format import decode_v1, encode_v1
from warehouse.release.issue import issue_release
from warehouse.release.manifest import ledger_digest


class MirrorTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'mirror.sqlite3'
        self.open_mirror()
        with closing(sqlite3.connect(Path(directory.name) / 'source.sqlite3')) as source:
            initialize_source(source)
            post_batch(source, 'A', 'meter-A', 'a-batch', [('cooling', 7), ('storm', 0)])
            post_batch(source, 'B', 'meter-B', 'b-batch', [('effluent', 4)])
            self.a1 = self.publish(source, 'a1', 'A', 1)
            self.b1 = self.publish(source, 'b1', 'B', 1)
            catalog = CatalogRepository(source)
            facts = FactRepository(source)
            for revision, liters in ((2, 8), (3, 9)):
                old = read_current_revision(catalog, facts, 'A').rows[0]
                amend_row(source, 'A', old.identity, old, 'cooling', liters)
                if revision == 3:
                    self.a3 = self.publish(source, 'a3', 'A', revision)
            self.a3_alias = self.publish(source, 'a3-alias', 'A', 3)

    def open_mirror(self, before_commit=None):
        self.connection = sqlite3.connect(self.path)
        self.addCleanup(self.connection.close)
        self.mirror = MirrorRepository(self.connection, before_commit=before_commit)

    @staticmethod
    def publish(connection, release_id, partition, revision):
        snapshot = freeze_partition(connection, 'fixed-' + release_id, partition, revision)
        return issue_release(connection, release_id, snapshot.snapshot_id)[1]

    def test_offline_audit(self):
        result = install_release(self.mirror, self.a3)
        self.assertEqual((result.receipt, result.current_revision), (Receipt('a3', 'A', 3), 3))
        install_release(self.mirror, self.b1)
        self.connection.close()
        self.open_mirror()
        alpha = audit_partition(self.mirror, 'A')
        beta = audit_partition(self.mirror, 'B')
        self.assertEqual(alpha, audit_release(self.mirror, 'a3'))
        self.assertEqual(beta, audit_release(self.mirror, 'b1'))
        self.assertEqual((alpha.manifest.revision, beta.manifest.revision), (3, 1))
        self.assertEqual([row.liters for row in alpha.rows], [9, 0])
        self.assertEqual(alpha.tally, LedgerTally(2, 9, (CategoryTotal('cooling', 1, 9), CategoryTotal('storm', 1, 0))))
        self.assertEqual(beta.tally, LedgerTally(1, 4, (CategoryTotal('effluent', 1, 4),)))
        self.assertEqual([row.provenance for row in alpha.rows],
                         [RowProvenance('A', 'meter-A', 'a-batch', 1, 1), RowProvenance('A', 'meter-A', 'a-batch', 2, 1)])
        self.assertEqual(beta.rows[0].provenance, RowProvenance('B', 'meter-B', 'b-batch', 1, 1))

    def test_selector_errors(self):
        with self.assertRaisesRegex(ValueError, '^unknown_partition$'):
            audit_partition(self.mirror, 'A')
        with self.assertRaisesRegex(ValueError, '^unreceived_release$'):
            audit_release(self.mirror, 'a1')

    def test_same_revision_aliases_and_issue_conflict(self):
        install_release(self.mirror, self.a3)
        result = install_release(self.mirror, self.a3_alias)
        self.assertEqual((result.receipt, result.current_revision), (Receipt('a3-alias', 'A', 3), 3))
        primary = audit_partition(self.mirror, 'A')
        alias = audit_release(self.mirror, 'a3-alias')
        self.assertEqual(primary.manifest.release_id, 'a3')
        self.assertEqual(alias.manifest.release_id, 'a3-alias')
        self.assertEqual((alias.rows, alias.tally), (primary.rows, primary.tally))
        self.assertEqual(self.mirror.find_receipt('a3-alias').artifact, self.a3_alias)
        manifest, records = decode_v1(self.a1)
        conflicting = encode_v1(replace(manifest, release_id='a3'), records)
        with self.assertRaisesRegex(ValueError, '^received_issue_conflict$'):
            install_release(self.mirror, conflicting)
        self.assertEqual(self.mirror.find_receipt('a3').artifact, self.a3)
        self.assertIsNone(self.mirror.find_revision_ledger('A', 1))
        self.assertEqual(audit_partition(self.mirror, 'A'), primary)

    def test_bad_tally(self):
        install_release(self.mirror, self.a1)
        old = audit_partition(self.mirror, 'A')
        manifest, records = decode_v1(self.a3)
        # Move one liter between classes while preserving the total and having a valid digest.
        categories = (CategoryTotal('cooling', 1, 8), CategoryTotal('storm', 1, 1))
        bad_classification = replace(records, tally=LedgerTally(2, 9, categories))
        bad_total = replace(records, tally=replace(records.tally, liters=10))
        for bad_ledger in (bad_classification, bad_total):
            with self.subTest(tally=bad_ledger.tally):
                changed = replace(manifest, ledger_sha256=ledger_digest(bad_ledger))
                with self.assertRaisesRegex(ValueError, '^bad_tally$'):
                    install_release(self.mirror, encode_v1(changed, bad_ledger))
                with self.assertRaisesRegex(ValueError, '^unreceived_release$'):
                    audit_release(self.mirror, 'a3')
                self.assertEqual(audit_partition(self.mirror, 'A'), old)

    def test_provenance_and_order(self):
        manifest, records = decode_v1(self.a1)
        first, second = records.rows
        other_partition = replace(first, provenance=replace(first.provenance, partition='B'))
        later_origin = replace(first, provenance=replace(first.provenance, accepted_revision=2))
        duplicate = replace(second, provenance=first.provenance)
        cases = (
            ((other_partition, second), 'provenance_partition_mismatch'),
            ((later_origin, second), 'provenance_revision_mismatch'),
            ((first, duplicate), 'duplicate_row_identity'),
            ((second, first), 'invalid_row_order'),
        )
        for rows, error in cases:
            with self.subTest(error=error):
                changed_records = replace(records, rows=rows)
                changed_manifest = replace(manifest, ledger_sha256=ledger_digest(changed_records))
                with self.assertRaisesRegex(ValueError, '^' + error + '$'):
                    install_release(self.mirror, encode_v1(changed_manifest, changed_records))
                self.assertIsNone(self.mirror.find_receipt('a1'))
                self.assertIsNone(self.mirror.get_current('A'))

    def test_wire_errors_leave_no_receipt(self):
        payload = json.loads(self.a1)
        payload['version'] = True
        manifest, records = decode_v1(self.a1)
        cases = (
            (b'\xff', 'invalid_artifact_json'),
            (b'{"version":1,' + self.a1[1:], 'duplicate_json_field'),
            (json.dumps(payload).encode('utf-8'), 'unsupported_artifact_version'),
            (encode_v1(replace(manifest, ledger_sha256='0' * 64), records), 'ledger_digest_mismatch'),
        )
        for artifact, error in cases:
            with self.subTest(error=error):
                with self.assertRaisesRegex(ValueError, '^' + error + '$'):
                    install_release(self.mirror, artifact)
                self.assertIsNone(self.mirror.find_receipt('a1'))
                self.assertIsNone(self.mirror.get_current('A'))

    def test_install_rollback(self):
        install_release(self.mirror, self.a1)
        install_release(self.mirror, self.b1)
        old_alpha = audit_partition(self.mirror, 'A')
        old_beta = audit_partition(self.mirror, 'B')
        self.connection.close()

        def fail_before_commit():
            raise sqlite3.OperationalError('shift commit interrupted')

        self.open_mirror(before_commit=fail_before_commit)
        with self.assertRaisesRegex(sqlite3.OperationalError, '^shift commit interrupted$'):
            install_release(self.mirror, self.a3)
        self.connection.close()
        self.open_mirror()
        self.assertEqual(audit_partition(self.mirror, 'A'), old_alpha)
        self.assertEqual(audit_partition(self.mirror, 'B'), old_beta)
        with self.assertRaisesRegex(ValueError, '^unreceived_release$'):
            audit_release(self.mirror, 'a3')
        self.assertIsNone(self.mirror.find_revision_ledger('A', 3))
        self.assertEqual(install_release(self.mirror, self.a3).current_revision, 3)
        self.assertEqual(audit_partition(self.mirror, 'A').tally.liters, 9)
        self.assertEqual(audit_partition(self.mirror, 'A'), audit_release(self.mirror, 'a3'))
        self.assertEqual(audit_partition(self.mirror, 'B'), old_beta)

    def test_old_release(self):
        install_release(self.mirror, self.a3)
        install_release(self.mirror, self.b1)
        old_receipt = install_release(self.mirror, self.a1)
        self.assertEqual((old_receipt.receipt.revision, old_receipt.current_revision), (1, 3))
        self.connection.close()
        self.open_mirror()
        self.assertEqual(audit_partition(self.mirror, 'A').tally.liters, 9)
        self.assertEqual(audit_partition(self.mirror, 'B').tally.liters, 4)
        old = audit_release(self.mirror, 'a1')
        self.assertEqual((old.manifest.revision, old.tally.count, old.tally.liters), (1, 2, 7))
        self.assertEqual([row.liters for row in old.rows], [7, 0])
        self.assertEqual(install_release(self.mirror, self.a1), old_receipt)

    def test_replay_after_advance_and_historical_revision_conflict(self):
        first = install_release(self.mirror, self.a1)
        install_release(self.mirror, self.a3)
        replay = install_release(self.mirror, self.a1)
        self.assertEqual(replay.receipt, first.receipt)
        self.assertEqual(replay.current_revision, 3)
        with closing(sqlite3.connect(':memory:')) as source:
            initialize_source(source)
            post_batch(source, 'A', 'meter-A', 'a-batch', [('cooling', 6), ('storm', 0)])
            different = self.publish(source, 'a1-other', 'A', 1)
        with self.assertRaisesRegex(ValueError, '^received_revision_conflict$'):
            install_release(self.mirror, different)
        with self.assertRaisesRegex(ValueError, '^unreceived_release$'):
            audit_release(self.mirror, 'a1-other')
        self.assertEqual(audit_release(self.mirror, 'a1').tally.liters, 7)
        self.assertEqual(audit_partition(self.mirror, 'A').tally.liters, 9)

    def test_empty_revision_can_be_installed(self):
        with closing(sqlite3.connect(':memory:')) as source:
            initialize_source(source)
            empty = self.publish(source, 'empty', 'A', 0)
        self.assertEqual(install_release(self.mirror, empty).current_revision, 0)
        audit = audit_partition(self.mirror, 'A')
        self.assertEqual((audit.rows, audit.tally), ((), LedgerTally(0, 0, ())))
