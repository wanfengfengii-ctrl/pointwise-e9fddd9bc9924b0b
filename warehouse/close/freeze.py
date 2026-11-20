from warehouse.catalog.partitions import require_identifier
from warehouse.catalog.repository import CatalogRepository
from warehouse.close.history import read_partition_history
from warehouse.close.repository import CloseRepository, FrozenSnapshot
from warehouse.close.tally import tally_rows
from warehouse.facts.repository import FactRepository


def freeze_partition(connection, snapshot_id: str, partition: str, revision: int) -> FrozenSnapshot:
    require_identifier(snapshot_id, 'snapshot_id')
    require_identifier(partition, 'partition')
    if type(revision) is not int or revision < 0:
        raise ValueError('unknown_revision')
    catalog = CatalogRepository(connection)
    facts = FactRepository(connection)
    close = CloseRepository(connection)
    with close.transaction():
        existing = close.find_snapshot(snapshot_id)
        if existing is not None:
            if (existing.partition, existing.revision) != (partition, revision):
                raise ValueError('snapshot_conflict')
            return existing
        rows = read_partition_history(catalog, facts, partition, revision)
        snapshot = FrozenSnapshot(snapshot_id, partition, revision, rows, tally_rows(rows))
        close.insert_snapshot(snapshot)
    return snapshot
