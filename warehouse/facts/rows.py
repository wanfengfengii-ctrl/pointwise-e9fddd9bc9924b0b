from dataclasses import dataclass, replace

from warehouse.catalog.partitions import require_identifier


def normalize_measurement(category: str, liters: int) -> tuple[str, int]:
    require_identifier(category, 'category')
    if type(liters) is not int:
        raise ValueError('invalid_liters')
    return category, liters


@dataclass(frozen=True)
class RowIdentity:
    partition: str
    source: str
    batch: str
    position: int

    def __post_init__(self):
        require_identifier(self.partition, 'partition')
        require_identifier(self.source, 'source')
        require_identifier(self.batch, 'batch')
        if type(self.position) is not int or self.position < 1:
            raise ValueError('invalid_position')


@dataclass(frozen=True)
class RowProvenance:
    partition: str
    source: str
    batch: str
    position: int
    accepted_revision: int

    def __post_init__(self):
        self.identity
        if type(self.accepted_revision) is not int or self.accepted_revision < 1:
            raise ValueError('invalid_accepted_revision')

    @property
    def identity(self) -> RowIdentity:
        return RowIdentity(self.partition, self.source, self.batch, self.position)


@dataclass(frozen=True)
class MeasurementRow:
    category: str
    liters: int
    provenance: RowProvenance

    def __post_init__(self):
        normalize_measurement(self.category, self.liters)
        if not isinstance(self.provenance, RowProvenance):
            raise ValueError('invalid_provenance')

    @property
    def identity(self) -> RowIdentity:
        return self.provenance.identity


def normalize_batch_rows(partition, source, batch, revision, readings) -> tuple[MeasurementRow, ...]:
    if not isinstance(readings, (list, tuple)) or not readings:
        raise ValueError('invalid_batch_rows')
    rows = []
    for position, reading in enumerate(readings, start=1):
        if not isinstance(reading, (list, tuple)) or len(reading) != 2:
            raise ValueError('invalid_measurement')
        category, liters = reading
        provenance = RowProvenance(partition, source, batch, position, revision)
        rows.append(MeasurementRow(category, liters, provenance))
    return tuple(rows)


def replace_measurement(row: MeasurementRow, category: str, liters: int) -> MeasurementRow:
    return replace(row, category=category, liters=liters)


def row_from_data(data) -> MeasurementRow:
    if not isinstance(data, dict) or set(data) != {'category', 'liters', 'provenance'}:
        raise ValueError('invalid_detail_record')
    provenance = data['provenance']
    if not isinstance(provenance, dict) or set(provenance) != {
        'partition', 'source', 'batch', 'position', 'accepted_revision'
    }:
        raise ValueError('invalid_provenance')
    return MeasurementRow(data['category'], data['liters'], RowProvenance(**provenance))
