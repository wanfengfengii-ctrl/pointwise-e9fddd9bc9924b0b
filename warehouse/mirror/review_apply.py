from warehouse.mirror.review_repository import MirrorReviewRepository, ReviewReceipt
from warehouse.mirror.review_verification import (
    verify_review_basis_message,
    verify_review_ledger,
    verify_revocation_message,
)
from warehouse.release.review_format import (
    BasisBinding,
    ReviewBasisMessage,
    ReviewRevocationMessage,
    ReviewedReleaseMessage,
    decode_review_message,
)


def _apply_basis(repository: MirrorReviewRepository, binding: BasisBinding) -> None:
    existing = repository.find_basis(binding.basis_id)
    if existing is not None:
        if existing != binding:
            raise ValueError('basis_binding_conflict')
        return
    if binding.parent_basis_id is not None:
        parent = repository.find_basis(binding.parent_basis_id)
        if parent is not None and (parent.partition != binding.partition or parent.revision >= binding.revision):
            raise ValueError('invalid_basis_parent')
    for child in repository.list_children(binding.basis_id):
        if child.partition != binding.partition or child.revision <= binding.revision:
            raise ValueError('invalid_basis_parent')
    repository.insert_basis(binding)


def _apply_revocation(repository: MirrorReviewRepository, notice_id: str,
                      binding: BasisBinding, artifact: bytes) -> None:
    existing = repository.find_revocation(notice_id)
    if existing is not None:
        stored_basis, stored_artifact = existing
        if stored_basis != binding.basis_id or stored_artifact != artifact:
            raise ValueError('revocation_binding_conflict')
        return
    known = repository.find_basis(binding.basis_id)
    if known is not None:
        if known != binding:
            raise ValueError('revocation_binding_conflict')
    else:
        # Revocations may arrive before their basis; register the exact binding so later evidence is checked.
        if binding.parent_basis_id is not None:
            parent = repository.find_basis(binding.parent_basis_id)
            if parent is not None and (parent.partition != binding.partition or parent.revision >= binding.revision):
                raise ValueError('invalid_basis_parent')
        for child in repository.list_children(binding.basis_id):
            if child.partition != binding.partition or child.revision <= binding.revision:
                raise ValueError('invalid_basis_parent')
        repository.insert_basis(binding)
    repository.insert_revocation(notice_id, binding.basis_id, artifact)


def _apply_release(repository: MirrorReviewRepository, verified) -> None:
    message = verified.message
    manifest = message.manifest
    basis = message.basis
    existing = repository.find_release(manifest.release_id)
    if existing is not None:
        if existing.artifact != verified.artifact:
            raise ValueError('received_issue_conflict')
        return
    for ancestor in message.ancestors:
        _apply_basis(repository, ancestor)
    _apply_basis(repository, basis)
    same_revision = repository.find_revision_ledger(manifest.partition, manifest.revision)
    if same_revision is not None and same_revision != manifest.ledger_sha256:
        raise ValueError('received_revision_conflict')
    receipt = ReviewReceipt(manifest.release_id, manifest.partition, manifest.revision, basis.basis_id)
    repository.insert_release(receipt, verified.artifact, manifest.ledger_sha256)


def apply_review_message(repository: MirrorReviewRepository, artifact: bytes) -> None:
    message = decode_review_message(artifact)
    with repository.transaction():
        if isinstance(message, ReviewedReleaseMessage):
            verified = verify_review_ledger(message, artifact)
            _apply_release(repository, verified)
        elif isinstance(message, ReviewBasisMessage):
            binding = verify_review_basis_message(message)
            _apply_basis(repository, binding)
        elif isinstance(message, ReviewRevocationMessage):
            notice_id, binding = verify_revocation_message(message)
            _apply_revocation(repository, notice_id, binding, artifact)
        else:  # pragma: no cover - decode_review_message already rejects unknown kinds
            raise ValueError('unsupported_artifact_kind')


def install_review_message(repository: MirrorReviewRepository, artifact: bytes) -> None:
    apply_review_message(repository, artifact)
    return None
