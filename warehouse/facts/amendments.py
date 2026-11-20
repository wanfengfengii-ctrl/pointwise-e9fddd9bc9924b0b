from dataclasses import dataclass

from warehouse.catalog.classification import require_partition_category
from warehouse.catalog.repository import CatalogRepository, require_registry
from warehouse.facts.repository import FactRepository
from warehouse.facts.revisions import read_current_revision
from warehouse.facts.rows import MeasurementRow, RowIdentity, replace_measurement


@dataclass(frozen=True)
class AmendmentReceipt:
    partition: str
    revision: int
    row: MeasurementRow


def amend_row(connection, partition, identity, expected, category, liters) -> AmendmentReceipt:
    if not isinstance(identity, RowIdentity) or identity.partition != partition:
        raise ValueError('unknown_row')
    if not isinstance(expected, MeasurementRow):
        raise ValueError('invalid_old_image')
    catalog = CatalogRepository(connection)
    facts = FactRepository(connection)
    with facts.transaction():
        view = read_current_revision(catalog, facts, partition)
        current = next((row for row in view.rows if row.identity == identity), None)
        if current is None:
            raise ValueError('unknown_row')
        if current != expected:
            raise ValueError('stale_old_image')
        replacement = replace_measurement(current, category, liters)
        registry = require_registry(catalog)
        require_partition_category(registry.classification_index(), partition, replacement.category)
        revision = view.revision + 1
        facts.append_amendment(partition, revision, replacement)
    return AmendmentReceipt(partition, revision, replacement)
