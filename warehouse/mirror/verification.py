from dataclasses import dataclass
from hashlib import sha256

from warehouse.close.tally import tally_rows
from warehouse.release.manifest import ReleaseManifest
from warehouse.release.records import LedgerRecords, canonical_ledger_bytes


@dataclass(frozen=True)
class VerifiedLedger:
    manifest: ReleaseManifest
    artifact: bytes
    canonical_ledger: bytes


def _verify_details(manifest: ReleaseManifest, records: LedgerRecords) -> None:
    seen_rows = set()
    seen_batches = set()
    last_batch = None
    last_revision = 0
    last_position = 0
    for row in records.rows:
        origin = row.provenance
        if origin.partition != manifest.partition:
            raise ValueError('provenance_partition_mismatch')
        if origin.accepted_revision > manifest.revision:
            raise ValueError('provenance_revision_mismatch')
        identity = row.identity
        if identity in seen_rows:
            raise ValueError('duplicate_row_identity')
        seen_rows.add(identity)
        batch = (identity.source, identity.batch)
        if origin.accepted_revision == last_revision:
            if batch != last_batch or identity.position != last_position + 1:
                raise ValueError('invalid_row_order')
        else:
            if origin.accepted_revision < last_revision or identity.position != 1 or batch in seen_batches:
                raise ValueError('invalid_row_order')
            seen_batches.add(batch)
        last_batch = batch
        last_revision = origin.accepted_revision
        last_position = identity.position


def verify_v1_ledger(manifest: ReleaseManifest, records: LedgerRecords, artifact: bytes) -> VerifiedLedger:
    ledger = canonical_ledger_bytes(records)
    if sha256(ledger).hexdigest() != manifest.ledger_sha256:
        raise ValueError('ledger_digest_mismatch')
    _verify_details(manifest, records)
    if tally_rows(records.rows) != records.tally:
        raise ValueError('bad_tally')
    return VerifiedLedger(manifest, artifact, ledger)
