from dataclasses import dataclass

from warehouse.mirror.verification import verify_v1_ledger
from warehouse.release.review_format import (
    BasisBinding,
    ReviewedReleaseMessage,
    ReviewBasisMessage,
    ReviewRevocationMessage,
)


@dataclass(frozen=True)
class VerifiedReviewLedger:
    message: ReviewedReleaseMessage
    artifact: bytes
    canonical_ledger: bytes


def verify_review_ledger(message: ReviewedReleaseMessage, artifact: bytes) -> VerifiedReviewLedger:
    manifest = message.manifest
    basis = message.basis
    verified = verify_v1_ledger(manifest, message.records, artifact)
    if manifest.partition != basis.partition or manifest.revision != basis.revision:
        raise ValueError('basis_manifest_conflict')
    if manifest.ledger_sha256 != basis.ledger_sha256:
        raise ValueError('basis_ledger_digest_conflict')
    _check_ancestor_structure(basis, message.ancestors)
    return VerifiedReviewLedger(message, artifact, verified.canonical_ledger)


def _check_ancestor_structure(basis: BasisBinding, ancestors: tuple[BasisBinding, ...]) -> None:
    expected_parent = basis.parent_basis_id
    seen = set()
    for ancestor in ancestors:
        if expected_parent is None or ancestor.basis_id != expected_parent or ancestor.basis_id in seen:
            raise ValueError('invalid_basis_ancestors')
        seen.add(ancestor.basis_id)
        if ancestor.partition != basis.partition or ancestor.revision >= basis.revision:
            raise ValueError('invalid_basis_parent')
        expected_parent = ancestor.parent_basis_id
    # A partial chain is accepted: the remaining ancestors can arrive as separate basis messages.


def verify_review_basis_message(message: ReviewBasisMessage) -> BasisBinding:
    return message.basis


def verify_revocation_message(message: ReviewRevocationMessage) -> tuple[str, BasisBinding]:
    return message.notice_id, message.basis
