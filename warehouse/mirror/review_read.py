from dataclasses import dataclass

from warehouse.catalog.partitions import require_identifier
from warehouse.close.tally import LedgerTally
from warehouse.mirror.review_repository import MirrorReviewRepository
from warehouse.release.manifest import ReleaseManifest
from warehouse.release.records import DetailRecord
from warehouse.release.review_format import BasisBinding, decode_review_message


ELIGIBLE = 'eligible'
PENDING = 'pending'
REVOKED = 'revoked'


@dataclass(frozen=True)
class AuditResult:
    manifest: ReleaseManifest
    rows: tuple[DetailRecord, ...]
    tally: LedgerTally


def _chain_status(repository: MirrorReviewRepository, binding: BasisBinding) -> str:
    current = binding
    seen = set()
    while True:
        if current.basis_id in seen:
            return REVOKED
        seen.add(current.basis_id)
        if repository.has_revocation_for_basis(current.basis_id):
            return REVOKED
        if current.parent_basis_id is None:
            return ELIGIBLE
        parent = repository.find_basis(current.parent_basis_id)
        if parent is None:
            return PENDING
        current = parent


def _audit(stored) -> AuditResult:
    message = decode_review_message(stored.artifact)
    return AuditResult(message.manifest, message.records.rows, message.records.tally)


def audit_reviewed_partition(repository: MirrorReviewRepository, partition: str) -> AuditResult | None:
    require_identifier(partition, 'partition')
    for stored in repository.list_releases(partition):
        binding = repository.find_basis(stored.receipt.basis_id)
        if binding is None:
            continue
        if _chain_status(repository, binding) == ELIGIBLE:
            return _audit(stored)
    return None


def audit_reviewed_release(repository: MirrorReviewRepository, release_id: str) -> tuple[AuditResult, str]:
    require_identifier(release_id, 'release_id')
    stored = repository.find_release(release_id)
    if stored is None:
        raise ValueError('unreceived_release')
    binding = repository.find_basis(stored.receipt.basis_id)
    if binding is None:
        status = PENDING
    else:
        status = _chain_status(repository, binding)
    return _audit(stored), status
