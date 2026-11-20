from dataclasses import dataclass

from warehouse.mirror.repository import MirrorRepository, Receipt
from warehouse.mirror.verification import VerifiedLedger


@dataclass(frozen=True)
class InstallResult:
    receipt: Receipt
    current_revision: int


def apply_release(repository: MirrorRepository, verified: VerifiedLedger) -> InstallResult:
    manifest = verified.manifest
    with repository.transaction():
        existing = repository.find_receipt(manifest.release_id)
        current = repository.get_current(manifest.partition)
        if existing is not None:
            if existing.artifact != verified.artifact:
                raise ValueError('received_issue_conflict')
            return InstallResult(existing.receipt, current.receipt.revision)

        same_revision = repository.find_revision_ledger(manifest.partition, manifest.revision)
        if same_revision is not None and same_revision != verified.canonical_ledger:
            raise ValueError('received_revision_conflict')
        receipt = Receipt(manifest.release_id, manifest.partition, manifest.revision)
        repository.insert_receipt(receipt, verified.artifact, verified.canonical_ledger)
        if current is None or receipt.revision > current.receipt.revision:
            repository.set_current(receipt)
            return InstallResult(receipt, receipt.revision)
        return InstallResult(receipt, current.receipt.revision)
