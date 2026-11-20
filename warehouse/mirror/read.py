from dataclasses import dataclass

from warehouse.catalog.partitions import require_identifier
from warehouse.close.tally import LedgerTally
from warehouse.mirror.repository import MirrorRepository, ReceivedArtifact
from warehouse.release.format import decode_v1
from warehouse.release.manifest import ReleaseManifest
from warehouse.release.records import DetailRecord


@dataclass(frozen=True)
class AuditResult:
    manifest: ReleaseManifest
    rows: tuple[DetailRecord, ...]
    tally: LedgerTally


def _read_artifact(received: ReceivedArtifact) -> AuditResult:
    manifest, records = decode_v1(received.artifact)
    return AuditResult(manifest, records.rows, records.tally)


def audit_partition(repository: MirrorRepository, partition: str) -> AuditResult:
    require_identifier(partition, 'partition')
    current = repository.get_current(partition)
    if current is None:
        raise ValueError('unknown_partition')
    return _read_artifact(current)


def audit_release(repository: MirrorRepository, release_id: str) -> AuditResult:
    require_identifier(release_id, 'release_id')
    received = repository.find_receipt(release_id)
    if received is None:
        raise ValueError('unreceived_release')
    return _read_artifact(received)
