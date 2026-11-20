from dataclasses import dataclass

from warehouse.catalog.partitions import require_identifier
from warehouse.close.repository import CloseRepository
from warehouse.release.format import encode_v1
from warehouse.release.manifest import build_manifest
from warehouse.release.records import LedgerRecords
from warehouse.release.repository import ReleaseRepository, StoredRelease


@dataclass(frozen=True)
class IssueReceipt:
    release_id: str
    snapshot_id: str
    partition: str
    revision: int
    ledger_sha256: str


def issue_release(connection, release_id: str, snapshot_id: str) -> tuple[IssueReceipt, bytes]:
    require_identifier(release_id, 'release_id')
    require_identifier(snapshot_id, 'snapshot_id')
    releases = ReleaseRepository(connection)
    close = CloseRepository(connection)
    with releases.transaction():
        release = releases.find_release(release_id)
        if release is None:
            snapshot = close.find_snapshot(snapshot_id)
            if snapshot is None:
                raise ValueError('unknown_snapshot')
            records = LedgerRecords(snapshot.rows, snapshot.tally)
            manifest = build_manifest(release_id, snapshot_id, snapshot.partition, snapshot.revision, records)
            release = StoredRelease(snapshot_id, manifest.partition, manifest.revision,
                                    manifest.ledger_sha256, encode_v1(manifest, records))
            releases.insert_release(release_id, release)
        elif release.snapshot_id != snapshot_id:
            raise ValueError('issue_binding_conflict')
    receipt = IssueReceipt(release_id, release.snapshot_id, release.partition, release.revision, release.ledger_sha256)
    return receipt, release.artifact
