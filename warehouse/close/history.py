from warehouse.facts.revisions import read_fact_revision
from warehouse.facts.rows import MeasurementRow


def read_partition_history(catalog, facts, partition: str, revision: int) -> tuple[MeasurementRow, ...]:
    view = read_fact_revision(catalog, facts, partition, revision)
    if (view.partition, view.revision) != (partition, revision):
        raise ValueError('history_binding_mismatch')
    return view.rows
