from dataclasses import dataclass

from warehouse.catalog.partitions import require_partition
from warehouse.catalog.repository import CatalogRepository, require_registry
from warehouse.facts.repository import BatchEvent, FactRepository
from warehouse.facts.rows import MeasurementRow, normalize_batch_rows, replace_measurement


@dataclass(frozen=True)
class RevisionView:
    partition: str
    revision: int
    rows: tuple[MeasurementRow, ...]


def read_fact_revision(catalog: CatalogRepository, facts: FactRepository,
                       partition: str, revision: int) -> RevisionView:
    require_partition(require_registry(catalog).partition_index(), partition)
    if type(revision) is not int or revision < 0 or revision > facts.head_revision(partition):
        raise ValueError('unknown_revision')
    rows = {}
    for event in facts.read_revision_events(partition, revision):
        if isinstance(event, BatchEvent):
            for row in normalize_batch_rows(partition, event.source, event.batch, event.revision, event.readings):
                rows[row.identity] = row
        else:
            # Updating an existing dictionary entry preserves its original insertion position.
            rows[event.identity] = replace_measurement(rows[event.identity], event.category, event.liters)
    return RevisionView(partition, revision, tuple(rows.values()))


def read_current_revision(catalog: CatalogRepository, facts: FactRepository, partition: str) -> RevisionView:
    require_partition(require_registry(catalog).partition_index(), partition)
    return read_fact_revision(catalog, facts, partition, facts.head_revision(partition))
