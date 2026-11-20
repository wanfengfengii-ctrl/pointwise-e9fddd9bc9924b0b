from dataclasses import asdict, dataclass
import json

from warehouse.catalog.partitions import require_identifier
from warehouse.release.manifest import ReleaseManifest, parse_manifest
from warehouse.release.records import LedgerRecords, canonical_json_bytes, decode_ledger_records


V2_FORMAT = 2

REVIEWED_RELEASE = 'reviewed_release'
REVIEW_REVOCATION = 'review_revocation'
REVIEW_BASIS = 'review_basis'


@dataclass(frozen=True)
class BasisBinding:
    basis_id: str
    partition: str
    revision: int
    ledger_sha256: str
    parent_basis_id: str | None


def _validate_binding(binding: BasisBinding) -> None:
    require_identifier(binding.basis_id, 'basis_id')
    require_identifier(binding.partition, 'partition')
    if type(binding.revision) is not int or not 0 <= binding.revision < 2**63:
        raise ValueError('invalid_basis_revision')
    digest = binding.ledger_sha256
    if type(digest) is not str or len(digest) != 64 or any(ch not in '0123456789abcdef' for ch in digest):
        raise ValueError('invalid_ledger_digest')
    if binding.parent_basis_id is not None:
        require_identifier(binding.parent_basis_id, 'parent_basis_id')


@dataclass(frozen=True)
class ReviewedReleaseMessage:
    manifest: ReleaseManifest
    basis: BasisBinding
    ancestors: tuple[BasisBinding, ...]
    records: LedgerRecords


@dataclass(frozen=True)
class ReviewRevocationMessage:
    notice_id: str
    basis: BasisBinding


@dataclass(frozen=True)
class ReviewBasisMessage:
    basis: BasisBinding


def binding_from_data(data) -> BasisBinding:
    if not isinstance(data, dict) or set(data) != {
        'basis_id', 'partition', 'revision', 'ledger_sha256', 'parent_basis_id'
    }:
        raise ValueError('invalid_basis_binding')
    binding = BasisBinding(**data)
    _validate_binding(binding)
    return binding


def binding_as_data(binding: BasisBinding) -> dict:
    return asdict(binding)


def _ledger_data(records: LedgerRecords) -> dict:
    return {
        'rows': [asdict(row) for row in records.rows],
        'tally': asdict(records.tally),
    }


def encode_review_v2(manifest: ReleaseManifest, basis: BasisBinding,
                     ancestors, records: LedgerRecords) -> bytes:
    return canonical_json_bytes({
        'version': V2_FORMAT,
        'kind': REVIEWED_RELEASE,
        'manifest': asdict(manifest),
        'basis': binding_as_data(basis),
        'ancestors': [binding_as_data(item) for item in ancestors],
        'ledger': _ledger_data(records),
    })


def encode_revocation(notice_id: str, basis: BasisBinding) -> bytes:
    require_identifier(notice_id, 'notice_id')
    return canonical_json_bytes({
        'version': V2_FORMAT,
        'kind': REVIEW_REVOCATION,
        'notice_id': notice_id,
        'basis': binding_as_data(basis),
    })


def encode_review_basis(basis: BasisBinding) -> bytes:
    return canonical_json_bytes({
        'version': V2_FORMAT,
        'kind': REVIEW_BASIS,
        'basis': binding_as_data(basis),
    })


def _unique_fields(pairs):
    fields = {}
    for key, value in pairs:
        if key in fields:
            raise ValueError('duplicate_json_field')
        fields[key] = value
    return fields


def _reject_constant(value):
    raise ValueError(f'invalid_json_constant: {value}')


def decode_review_message(artifact: bytes):
    if type(artifact) is not bytes:
        raise ValueError('invalid_artifact_bytes')
    try:
        data = json.loads(artifact.decode('utf-8'), object_pairs_hook=_unique_fields, parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError('invalid_artifact_json') from error
    if not isinstance(data, dict):
        raise ValueError('invalid_artifact_envelope')
    if type(data.get('version')) is not int or data['version'] != V2_FORMAT:
        raise ValueError('unsupported_artifact_version')
    kind = data.get('kind')
    if kind == REVIEWED_RELEASE:
        if set(data) != {'version', 'kind', 'manifest', 'basis', 'ancestors', 'ledger'}:
            raise ValueError('invalid_artifact_envelope')
        if not isinstance(data['ancestors'], list):
            raise ValueError('invalid_basis_ancestors')
        ancestors = tuple(binding_from_data(item) for item in data['ancestors'])
        return ReviewedReleaseMessage(
            parse_manifest(data['manifest']),
            binding_from_data(data['basis']),
            ancestors,
            decode_ledger_records(data['ledger']),
        )
    if kind == REVIEW_REVOCATION:
        if set(data) != {'version', 'kind', 'notice_id', 'basis'}:
            raise ValueError('invalid_artifact_envelope')
        require_identifier(data['notice_id'], 'notice_id')
        return ReviewRevocationMessage(data['notice_id'], binding_from_data(data['basis']))
    if kind == REVIEW_BASIS:
        if set(data) != {'version', 'kind', 'basis'}:
            raise ValueError('invalid_artifact_envelope')
        return ReviewBasisMessage(binding_from_data(data['basis']))
    raise ValueError('unsupported_artifact_kind')
