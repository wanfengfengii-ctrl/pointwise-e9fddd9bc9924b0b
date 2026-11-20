from dataclasses import asdict, dataclass
import json

from warehouse.close.tally import LedgerTally, tally_from_data
from warehouse.facts.rows import MeasurementRow, row_from_data


DetailRecord = MeasurementRow


@dataclass(frozen=True)
class LedgerRecords:
    rows: tuple[DetailRecord, ...]
    tally: LedgerTally


def encode_ledger_records(records: LedgerRecords) -> dict:
    return asdict(records)


def decode_ledger_records(data) -> LedgerRecords:
    if not isinstance(data, dict) or set(data) != {'rows', 'tally'} or not isinstance(data['rows'], list):
        raise ValueError('invalid_ledger_records')
    return LedgerRecords(tuple(row_from_data(row) for row in data['rows']), tally_from_data(data['tally']))


def canonical_json_bytes(data) -> bytes:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def canonical_ledger_bytes(records: LedgerRecords) -> bytes:
    return canonical_json_bytes(encode_ledger_records(records))
