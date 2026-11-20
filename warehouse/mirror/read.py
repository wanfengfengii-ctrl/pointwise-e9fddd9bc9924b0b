from dataclasses import dataclass

from warehouse.catalog.partitions import require_identifier
from warehouse.close.tally import LedgerTally
from warehouse.mirror.repository import MirrorRepository, ReceivedArtifact
from warehouse.mirror.review_read import (
    AuditResult as ReviewAuditResult,
    audit_reviewed_partition as _audit_reviewed_partition,
    audit_reviewed_release as _audit_reviewed_release,
)
from warehouse.mirror.review_repository import MirrorReviewRepository
from warehouse.release.format import decode_v1
from warehouse.release.manifest import ReleaseManifest
from warehouse.release.records import DetailRecord


@dataclass(frozen=True)
class AuditResult:
    manifest: ReleaseManifest
    rows: tuple[DetailRecord, ...]
    tally: LedgerTally


def _from_review(result: ReviewAuditResult | None) -> AuditResult | None:
    if result is None:
        return None
    return AuditResult(result.manifest, result.rows, result.tally)


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


def audit_reviewed_partition(repository: MirrorReviewRepository, partition: str) -> AuditResult | None:
    return _from_review(_audit_reviewed_partition(repository, partition))


def audit_reviewed_release(repository: MirrorReviewRepository, release_id: str) -> tuple[AuditResult, str]:
    result, status = _audit_reviewed_release(repository, release_id)
    return _from_review(result), status
