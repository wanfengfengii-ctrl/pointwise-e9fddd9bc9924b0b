from dataclasses import asdict
import json

from warehouse.release.manifest import ReleaseManifest, parse_manifest
from warehouse.release.records import LedgerRecords, canonical_json_bytes, decode_ledger_records, encode_ledger_records


V1_FORMAT = 1


def encode_v1(manifest: ReleaseManifest, records: LedgerRecords) -> bytes:
    return canonical_json_bytes({
        'version': V1_FORMAT,
        'manifest': asdict(manifest),
        'ledger': encode_ledger_records(records),
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


def decode_v1(artifact: bytes) -> tuple[ReleaseManifest, LedgerRecords]:
    if type(artifact) is not bytes:
        raise ValueError('invalid_artifact_bytes')
    try:
        data = json.loads(artifact.decode('utf-8'), object_pairs_hook=_unique_fields, parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError('invalid_artifact_json') from error
    if not isinstance(data, dict) or set(data) != {'version', 'manifest', 'ledger'}:
        raise ValueError('invalid_artifact_envelope')
    if type(data['version']) is not int or data['version'] != V1_FORMAT:
        raise ValueError('unsupported_artifact_version')
    return parse_manifest(data['manifest']), decode_ledger_records(data['ledger'])
