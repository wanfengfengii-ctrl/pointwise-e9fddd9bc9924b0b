from dataclasses import dataclass

from warehouse.catalog.classification import require_partition_category
from warehouse.catalog.partitions import require_identifier, require_partition
from warehouse.catalog.repository import CatalogRepository, require_registry
from warehouse.catalog.sources import require_source_partition
from warehouse.facts.repository import FactRepository
from warehouse.facts.rows import normalize_batch_rows


@dataclass(frozen=True)
class BatchReceipt:
    partition: str
    source: str
    batch: str
    revision: int
    row_count: int


def post_batch(connection, partition, source, batch, readings) -> BatchReceipt:
    require_identifier(batch, 'batch')
    catalog = CatalogRepository(connection)
    facts = FactRepository(connection)
    with facts.transaction():
        registry = require_registry(catalog)
        require_partition(registry.partition_index(), partition)
        require_source_partition(registry.source_index(), source, partition)
        accepted = facts.find_batch(partition, source, batch)
        revision = accepted.revision if accepted is not None else facts.head_revision(partition) + 1
        rows = normalize_batch_rows(partition, source, batch, revision, readings)
        if accepted is not None:
            submitted = tuple((row.category, row.liters) for row in rows)
            if accepted.readings != submitted:
                raise ValueError('batch_conflict')
        else:
            classifications = registry.classification_index()
            for row in rows:
                require_partition_category(classifications, partition, row.category)
            facts.append_batch(partition, source, batch, revision, rows)
    return BatchReceipt(partition, source, batch, revision, len(rows))
