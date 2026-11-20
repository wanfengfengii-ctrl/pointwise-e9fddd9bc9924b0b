import sqlite3
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from tests import initialize_source
from warehouse.catalog.repository import CatalogRepository
from warehouse.close.freeze import freeze_partition
from warehouse.facts.amendments import amend_row
from warehouse.facts.batches import post_batch
from warehouse.facts.repository import FactRepository
from warehouse.facts.revisions import read_current_revision
from warehouse.mirror.admission import install_release, install_review_message
from warehouse.mirror.read import (
    audit_partition,
    audit_reviewed_partition,
    audit_reviewed_release,
    audit_release,
)
from warehouse.mirror.repository import MirrorRepository
from warehouse.mirror.review_repository import MirrorReviewRepository
from warehouse.release.issue import (
    export_review_basis,
    issue_release,
    issue_revocation,
    issue_reviewed,
)
from warehouse.release.review_format import (
    BasisBinding,
    decode_review_message,
    encode_review_basis,
    encode_review_v2,
)


def build_chain(connection):
    initialize_source(connection)
    post_batch(connection, 'A', 'meter-A', 'shift', [('cooling', 4), ('storm', 0)])
    catalog = CatalogRepository(connection)
    facts = FactRepository(connection)
    snapshots = {1: freeze_partition(connection, 'snap-1', 'A', 1)}
    for revision, liters in ((2, 12), (3, 13)):
        row = read_current_revision(catalog, facts, 'A').rows[0]
        amend_row(connection, 'A', row.identity, row, row.category, liters)
        snapshots[revision] = freeze_partition(connection, f'snap-{revision}', 'A', revision)
    return snapshots


class ReviewSourceTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.addCleanup(self.connection.close)
        self.snapshots = build_chain(self.connection)

    def test_issue_reviewed_artifacts_and_reissue_bytes(self):
        first = issue_reviewed(self.connection, 'Q', self.snapshots[2].snapshot_id, 'basis-Q')
        again = issue_reviewed(self.connection, 'S', self.snapshots[3].snapshot_id, 'basis-S', 'basis-Q')
        self.assertIsInstance(first, bytes)
        message = decode_review_message(first)
        self.assertEqual((message.manifest.release_id, message.manifest.partition, message.manifest.revision),
                         ('Q', 'A', 2))
        self.assertEqual(message.basis, BasisBinding('basis-Q', 'A', 2, message.manifest.ledger_sha256, None))
        self.assertEqual(message.ancestors, ())
        s_message = decode_review_message(again)
        self.assertEqual(s_message.basis.parent_basis_id, 'basis-Q')
        self.assertEqual([item.basis_id for item in s_message.ancestors], ['basis-Q'])
        # Same id and same parameters return the original bytes.
        self.assertEqual(
            issue_reviewed(self.connection, 'Q', self.snapshots[2].snapshot_id, 'basis-Q'),
            first,
        )
        self.assertEqual(
            issue_reviewed(self.connection, 'S', self.snapshots[3].snapshot_id, 'basis-S', 'basis-Q'),
            again,
        )

    def test_independent_basis_for_same_revision(self):
        q = issue_reviewed(self.connection, 'Q', self.snapshots[2].snapshot_id, 'basis-Q')
        r = issue_reviewed(self.connection, 'R', self.snapshots[2].snapshot_id, 'basis-R')
        q_message = decode_review_message(q)
        r_message = decode_review_message(r)
        self.assertEqual(q_message.basis.ledger_sha256, r_message.basis.ledger_sha256)
        self.assertNotEqual(q_message.basis.basis_id, r_message.basis.basis_id)
        self.assertEqual(q_message.manifest.ledger_sha256, r_message.manifest.ledger_sha256)

    def test_basis_binding_conflicts(self):
        issue_reviewed(self.connection, 'Q', self.snapshots[2].snapshot_id, 'basis-Q')
        with self.assertRaisesRegex(ValueError, '^basis_binding_conflict$'):
            issue_reviewed(self.connection, 'Q2', self.snapshots[3].snapshot_id, 'basis-Q')
        with self.assertRaisesRegex(ValueError, '^issue_binding_conflict$'):
            issue_reviewed(self.connection, 'Q', self.snapshots[3].snapshot_id, 'basis-Q')
        with self.assertRaisesRegex(ValueError, '^issue_binding_conflict$'):
            issue_reviewed(self.connection, 'Q', self.snapshots[2].snapshot_id, 'basis-other')

    def test_parent_must_be_earlier_same_partition(self):
        issue_reviewed(self.connection, 'Q', self.snapshots[2].snapshot_id, 'basis-Q')
        with self.assertRaisesRegex(ValueError, '^invalid_basis_parent$'):
            issue_reviewed(self.connection, 'bad', self.snapshots[1].snapshot_id, 'basis-bad', 'basis-Q')
        with self.assertRaisesRegex(ValueError, '^unknown_review_basis$'):
            issue_reviewed(self.connection, 'bad', self.snapshots[3].snapshot_id, 'basis-missing', 'basis-gone')
        with self.assertRaisesRegex(ValueError, '^unknown_snapshot$'):
            issue_reviewed(self.connection, 'bad', 'missing-snapshot', 'basis-x')

    def test_revocation_and_basis_export(self):
        issue_reviewed(self.connection, 'Q', self.snapshots[2].snapshot_id, 'basis-Q')
        notice = issue_revocation(self.connection, '撤Q', 'basis-Q')
        message = decode_review_message(notice)
        self.assertEqual(message.notice_id, '撤Q')
        self.assertEqual(message.basis.basis_id, 'basis-Q')
        self.assertEqual(issue_revocation(self.connection, '撤Q', 'basis-Q'), notice)
        with self.assertRaisesRegex(ValueError, '^revocation_binding_conflict$'):
            issue_revocation(self.connection, '撤Q', 'basis-other')
        with self.assertRaisesRegex(ValueError, '^unknown_review_basis$'):
            issue_revocation(self.connection, '撤2', 'basis-gone')
        # Already-issued releases remain retrievable after revocation.
        basis_bytes = export_review_basis(self.connection, 'basis-Q')
        self.assertEqual(export_review_basis(self.connection, 'basis-Q'), basis_bytes)
        basis_message = decode_review_message(basis_bytes)
        self.assertEqual(basis_message.basis, message.basis)
        with self.assertRaisesRegex(ValueError, '^unknown_review_basis$'):
            export_review_basis(self.connection, 'basis-gone')


class ReviewMirrorTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'mirror.sqlite3'
        source_path = Path(directory.name) / 'source.sqlite3'
        self.source_path = source_path
        with closing(sqlite3.connect(source_path)) as source:
            self.snapshots = build_chain(source)
            self.artifacts = {
                'P': issue_reviewed(source, 'P', self.snapshots[1].snapshot_id, 'basis-P'),
                'Q': issue_reviewed(source, 'Q', self.snapshots[2].snapshot_id, 'basis-Q'),
                'S': issue_reviewed(source, 'S', self.snapshots[3].snapshot_id, 'basis-S', 'basis-Q'),
                'R': issue_reviewed(source, 'R', self.snapshots[2].snapshot_id, 'basis-R'),
            }
            self.artifacts['撤Q'] = issue_revocation(source, '撤Q', 'basis-Q')
            self.basis_q = export_review_basis(source, 'basis-Q')
        self.open_mirror()

    def open_mirror(self, before_commit=None):
        self.connection = sqlite3.connect(self.path)
        self.addCleanup(self.connection.close)
        self.mirror = MirrorReviewRepository(self.connection, before_commit=before_commit)

    def install(self, name):
        return install_review_message(self.mirror, self.artifacts[name])

    def test_full_review_handoff_fallback_recovery_and_reopen(self):
        self.assertIsNone(self.install('P'))
        self.install('Q')
        self.install('S')
        formal = audit_reviewed_partition(self.mirror, 'A')
        self.assertEqual((formal.manifest.release_id, formal.tally.liters), ('S', 13))
        self.assertEqual([row.liters for row in formal.rows], [13, 0])
        self.assertEqual(audit_reviewed_release(self.mirror, 'S')[1], 'eligible')
        self.install('撤Q')
        formal = audit_reviewed_partition(self.mirror, 'A')
        self.assertEqual((formal.manifest.release_id, formal.tally.liters), ('P', 4))
        self.assertEqual(audit_reviewed_release(self.mirror, 'Q')[1], 'revoked')
        self.assertEqual(audit_reviewed_release(self.mirror, 'S')[1], 'revoked')
        self.install('R')
        formal = audit_reviewed_partition(self.mirror, 'A')
        self.assertEqual((formal.manifest.release_id, formal.tally.liters), ('R', 12))
        self.connection.close()
        self.open_mirror()
        audit, status = audit_reviewed_release(self.mirror, 'S')
        self.assertEqual(status, 'revoked')
        self.assertEqual([row.liters for row in audit.rows], [13, 0])
        self.assertEqual([(row.provenance.source, row.provenance.batch, row.provenance.position,
                           row.provenance.accepted_revision) for row in audit.rows],
                         [('meter-A', 'shift', 1, 1), ('meter-A', 'shift', 2, 1)])
        self.assertEqual(audit.tally.categories[0].category, 'cooling')
        formal = audit_reviewed_partition(self.mirror, 'A')
        self.assertEqual((formal.manifest.release_id, formal.tally.liters), ('R', 12))

    def test_revocation_arrives_first_and_replays_are_idempotent(self):
        self.install('P')
        self.install('撤Q')
        self.assertEqual(audit_reviewed_partition(self.mirror, 'A').manifest.release_id, 'P')
        self.install('S')
        self.install('Q')
        self.assertEqual(audit_reviewed_release(self.mirror, 'Q')[1], 'revoked')
        self.assertEqual(audit_reviewed_release(self.mirror, 'S')[1], 'revoked')
        self.assertEqual(audit_reviewed_partition(self.mirror, 'A').manifest.release_id, 'P')
        self.install('R')
        self.assertEqual(audit_reviewed_partition(self.mirror, 'A').tally.liters, 12)
        for name in ('Q', 'S', '撤Q'):
            self.assertIsNone(self.install(name))
        self.assertEqual(audit_reviewed_partition(self.mirror, 'A').manifest.release_id, 'R')

    def test_split_delivery_pending_then_eligible(self):
        self.install('P')
        s_message = decode_review_message(self.artifacts['S'])
        partial = encode_review_v2(s_message.manifest, s_message.basis, (), s_message.records)
        install_review_message(self.mirror, partial)
        audit, status = audit_reviewed_release(self.mirror, 'S')
        self.assertEqual(status, 'pending')
        self.assertEqual(audit.tally.liters, 13)
        self.assertEqual(audit_reviewed_partition(self.mirror, 'A').manifest.release_id, 'P')
        with self.assertRaisesRegex(ValueError, '^unreceived_release$'):
            audit_reviewed_release(self.mirror, 'Q')
        install_review_message(self.mirror, self.basis_q)
        self.assertEqual(audit_reviewed_release(self.mirror, 'S')[1], 'eligible')
        self.assertEqual(audit_reviewed_partition(self.mirror, 'A').manifest.release_id, 'S')

    def test_bundled_ancestors_make_release_immediately_eligible(self):
        # S already carries the full ancestor chain from the source.
        self.install('P')
        self.install('S')
        self.assertEqual(audit_reviewed_release(self.mirror, 'S')[1], 'eligible')
        self.assertEqual(audit_reviewed_partition(self.mirror, 'A').manifest.release_id, 'S')
        with self.assertRaisesRegex(ValueError, '^unreceived_release$'):
            audit_reviewed_release(self.mirror, 'Q')

    def test_same_revision_chooses_smallest_release_id(self):
        self.install('R')
        self.install('Q')
        self.assertEqual(audit_reviewed_partition(self.mirror, 'A').manifest.release_id, 'Q')

    def test_replays_and_conflicts_leave_state_untouched(self):
        self.install('P')
        self.install('撤Q')
        # Identical bytes are legal replays.
        self.assertIsNone(install_review_message(self.mirror, self.artifacts['P']))
        tampered = self.artifacts['P'][:-1] + (b' ' if self.artifacts['P'][-1:] != b' ' else b'x')
        with self.assertRaises(ValueError):
            install_review_message(self.mirror, tampered)
        # A V2 release id cannot be rebound to different bytes.
        message = decode_review_message(self.artifacts['P'])
        rebound = encode_review_v2(
            replace(message.manifest, snapshot_id='other-snap'),
            message.basis, message.ancestors, message.records,
        )
        with self.assertRaisesRegex(ValueError, '^received_issue_conflict$'):
            install_review_message(self.mirror, rebound)
        self.assertEqual(audit_reviewed_partition(self.mirror, 'A').manifest.release_id, 'P')

    def test_basis_binding_conflict_on_admission(self):
        self.install('Q')
        message = decode_review_message(self.basis_q)
        changed = encode_review_basis(replace(message.basis, ledger_sha256='0' * 64))
        with self.assertRaisesRegex(ValueError, '^basis_binding_conflict$'):
            install_review_message(self.mirror, changed)

    def test_revocation_binding_conflict(self):
        self.install('Q')
        notice = decode_review_message(self.artifacts['撤Q'])
        changed = replace(notice.basis, ledger_sha256='0' * 64)
        from warehouse.release.review_format import encode_revocation
        bad = encode_revocation(notice.notice_id, changed)
        with self.assertRaisesRegex(ValueError, '^revocation_binding_conflict$'):
            install_review_message(self.mirror, bad)
        self.assertEqual(audit_reviewed_release(self.mirror, 'Q')[1], 'eligible')

    def test_same_revision_ledger_conflict(self):
        self.install('Q')
        # Build a second source whose revision 2 ledger differs.
        with closing(sqlite3.connect(':memory:')) as other:
            initialize_source(other)
            post_batch(other, 'A', 'meter-A', 'shift', [('cooling', 5), ('storm', 0)])
            row = read_current_revision(CatalogRepository(other), FactRepository(other), 'A').rows[0]
            amend_row(other, 'A', row.identity, row, row.category, 6)
            other_snapshot = freeze_partition(other, 'other-snap', 'A', 2)
            artifact = issue_reviewed(other, 'Q-other', other_snapshot.snapshot_id, 'basis-other')
        with self.assertRaisesRegex(ValueError, '^received_revision_conflict$'):
            install_review_message(self.mirror, artifact)
        with self.assertRaisesRegex(ValueError, '^unreceived_release$'):
            audit_reviewed_release(self.mirror, 'Q-other')

    def test_bad_wire_messages_are_rejected(self):
        cases = (
            (b'\xff', 'invalid_artifact_json'),
            (b'{"version": 1, "kind": "reviewed_release"}', 'unsupported_artifact_version'),
            (b'{"version": 2, "kind": "unknown"}', 'unsupported_artifact_kind'),
            (b'{"version": 2}', 'unsupported_artifact_kind'),
            (b'{"version": 2, "kind": "reviewed_release"}', 'invalid_artifact_envelope'),
        )
        for artifact, error in cases:
            with self.subTest(error=error):
                with self.assertRaisesRegex(ValueError, '^' + error + '$'):
                    install_review_message(self.mirror, artifact)
        self.assertEqual(audit_reviewed_partition(self.mirror, 'A'), None)

    def test_rollback_on_commit_failure(self):
        self.install('P')
        self.install('Q')
        self.install('S')
        self.assertEqual(audit_reviewed_partition(self.mirror, 'A').tally.liters, 13)
        self.connection.close()

        def fail_commit():
            raise sqlite3.OperationalError('shift commit interrupted')

        self.open_mirror(before_commit=fail_commit)
        with self.assertRaisesRegex(sqlite3.OperationalError, '^shift commit interrupted$'):
            self.install('撤Q')
        self.connection.close()
        self.open_mirror()
        self.assertEqual(audit_reviewed_partition(self.mirror, 'A').tally.liters, 13)
        self.install('撤Q')
        self.assertEqual(audit_reviewed_partition(self.mirror, 'A').tally.liters, 4)

    def test_v1_and_v2_are_separate_namespaces(self):
        with closing(sqlite3.connect(self.source_path)) as source:
            v1 = issue_release(source, 'P', self.snapshots[1].snapshot_id)[1]
        v1_mirror = MirrorRepository(self.connection)
        install_release(v1_mirror, v1)
        # V1 install does not create V2 evidence.
        self.assertIsNone(audit_reviewed_partition(self.mirror, 'A'))
        with self.assertRaisesRegex(ValueError, '^unreceived_release$'):
            audit_reviewed_release(self.mirror, 'P')
        # V1 current still works through the V1 entries.
        self.assertEqual(audit_partition(v1_mirror, 'A').tally.liters, 4)
        with self.assertRaisesRegex(ValueError, '^unreceived_release$'):
            audit_release(v1_mirror, 'Q')
        # A V2 artifact is not a V1 envelope.
        with self.assertRaisesRegex(ValueError, '^invalid_artifact_envelope$'):
            install_release(v1_mirror, self.artifacts['P'])

    def test_v2_releases_keep_original_order_and_tally(self):
        self.install('S')
        audit, _ = audit_reviewed_release(self.mirror, 'S')
        self.assertEqual(
            [row.identity.position for row in audit.rows],
            [1, 2],
        )
        self.assertEqual(
            [(total.category, total.count, total.liters) for total in audit.tally.categories],
            [('cooling', 1, 13), ('storm', 1, 0)],
        )


if __name__ == '__main__':
    unittest.main()
