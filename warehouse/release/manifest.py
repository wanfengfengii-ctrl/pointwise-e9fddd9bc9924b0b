from dataclasses import dataclass
from hashlib import sha256

from warehouse.catalog.partitions import require_identifier
from warehouse.release.records import LedgerRecords, canonical_ledger_bytes


@dataclass(frozen=True)
class ReleaseManifest:
    release_id: str
    snapshot_id: str
    partition: str
    revision: int
    ledger_sha256: str

    def __post_init__(self):
        require_identifier(self.release_id, 'release_id')
        require_identifier(self.snapshot_id, 'snapshot_id')
        require_identifier(self.partition, 'partition')
        if type(self.revision) is not int or not 0 <= self.revision < 2**63:
            raise ValueError('invalid_release_revision')
        digest = self.ledger_sha256
        if type(digest) is not str or len(digest) != 64 or any(ch not in '0123456789abcdef' for ch in digest):
            raise ValueError('invalid_ledger_digest')


def ledger_digest(records: LedgerRecords) -> str:
    return sha256(canonical_ledger_bytes(records)).hexdigest()


def build_manifest(release_id, snapshot_id, partition, revision, records: LedgerRecords) -> ReleaseManifest:
    return ReleaseManifest(release_id, snapshot_id, partition, revision, ledger_digest(records))


def parse_manifest(data) -> ReleaseManifest:
    if not isinstance(data, dict) or set(data) != {'release_id', 'snapshot_id', 'partition', 'revision', 'ledger_sha256'}:
        raise ValueError('invalid_release_manifest')
    return ReleaseManifest(**data)
