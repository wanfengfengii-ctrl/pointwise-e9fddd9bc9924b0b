from dataclasses import dataclass

from warehouse.catalog.partitions import require_identifier
from warehouse.close.repository import CloseRepository
from warehouse.release.format import encode_v1
from warehouse.release.manifest import build_manifest, ledger_digest
from warehouse.release.records import LedgerRecords
from warehouse.release.repository import ReleaseRepository, StoredRelease
from warehouse.release.review_format import BasisBinding, encode_review_basis, encode_review_v2, encode_revocation
from warehouse.release.review_repository import ReviewSourceRepository


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


def _ancestor_chain(repository: ReviewSourceRepository, basis: BasisBinding) -> tuple[BasisBinding, ...]:
    chain = []
    parent_id = basis.parent_basis_id
    seen = set()
    while parent_id is not None:
        if parent_id in seen:
            raise ValueError('invalid_basis_parent')
        seen.add(parent_id)
        parent = repository.find_basis(parent_id)
        if parent is None:
            raise ValueError('unknown_review_basis')
        chain.append(parent)
        parent_id = parent.parent_basis_id
    return tuple(chain)


def _ensure_basis(repository: ReviewSourceRepository, basis_id: str, parent_basis_id,
                  partition: str, revision: int, digest: str) -> BasisBinding:
    binding = BasisBinding(basis_id, partition, revision, digest, parent_basis_id)
    existing = repository.find_basis(basis_id)
    if existing is not None:
        if existing != binding:
            raise ValueError('basis_binding_conflict')
        return existing
    if parent_basis_id is not None:
        parent = repository.find_basis(parent_basis_id)
        if parent is None:
            raise ValueError('unknown_review_basis')
        if parent.partition != partition or parent.revision >= revision:
            raise ValueError('invalid_basis_parent')
    repository.insert_basis(binding)
    return binding


def issue_reviewed(connection, release_id: str, snapshot_id: str,
                   basis_id: str, parent_basis_id: str | None = None) -> bytes:
    require_identifier(release_id, 'release_id')
    require_identifier(snapshot_id, 'snapshot_id')
    require_identifier(basis_id, 'basis_id')
    if parent_basis_id is not None:
        require_identifier(parent_basis_id, 'parent_basis_id')
    reviews = ReviewSourceRepository(connection)
    close = CloseRepository(connection)
    with reviews.transaction():
        existing = reviews.find_release(release_id)
        if existing is not None:
            stored_snapshot, stored_basis, artifact = existing
            stored = reviews.find_basis(stored_basis)
            if stored_snapshot != snapshot_id or stored is None or stored.basis_id != basis_id \
                    or stored.parent_basis_id != parent_basis_id:
                raise ValueError('issue_binding_conflict')
            return artifact
        snapshot = close.find_snapshot(snapshot_id)
        if snapshot is None:
            raise ValueError('unknown_snapshot')
        records = LedgerRecords(snapshot.rows, snapshot.tally)
        digest = ledger_digest(records)
        basis = _ensure_basis(reviews, basis_id, parent_basis_id,
                              snapshot.partition, snapshot.revision, digest)
        manifest = build_manifest(release_id, snapshot_id, snapshot.partition, snapshot.revision, records)
        ancestors = _ancestor_chain(reviews, basis)
        artifact = encode_review_v2(manifest, basis, ancestors, records)
        reviews.insert_release(release_id, snapshot_id, basis_id, artifact)
    return artifact


def issue_revocation(connection, notice_id: str, basis_id: str) -> bytes:
    require_identifier(notice_id, 'notice_id')
    require_identifier(basis_id, 'basis_id')
    reviews = ReviewSourceRepository(connection)
    with reviews.transaction():
        existing = reviews.find_revocation(notice_id)
        if existing is not None:
            stored_basis, artifact = existing
            if stored_basis != basis_id:
                raise ValueError('revocation_binding_conflict')
            return artifact
        basis = reviews.find_basis(basis_id)
        if basis is None:
            raise ValueError('unknown_review_basis')
        artifact = encode_revocation(notice_id, basis)
        reviews.insert_revocation(notice_id, basis_id, artifact)
    return artifact


def export_review_basis(connection, basis_id: str) -> bytes:
    require_identifier(basis_id, 'basis_id')
    reviews = ReviewSourceRepository(connection)
    with reviews.transaction():
        cached = reviews.find_basis_export(basis_id)
        if cached is not None:
            return cached
        basis = reviews.find_basis(basis_id)
        if basis is None:
            raise ValueError('unknown_review_basis')
        artifact = encode_review_basis(basis)
        reviews.insert_basis_export(basis_id, artifact)
    return artifact
