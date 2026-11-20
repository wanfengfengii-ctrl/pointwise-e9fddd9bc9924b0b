from warehouse.catalog.classification import index_classifications
from warehouse.catalog.partitions import index_partitions
from warehouse.catalog.repository import CatalogRepository, Registry
from warehouse.catalog.sources import index_sources


def initialize_registry(connection, partitions, sources, classifications) -> int:
    partition_index = index_partitions(partitions)
    source_index = index_sources(sources, partition_index)
    classification_index = index_classifications(classifications, partition_index)
    catalog = CatalogRepository(connection)
    with catalog.transaction():
        existing = catalog.load_registry()
        registry = Registry(
            existing.version if existing is not None else 1,
            tuple(partition_index[key] for key in sorted(partition_index)),
            tuple(source_index[key] for key in sorted(source_index)),
            tuple(classification_index[key] for key in sorted(classification_index)),
        )
        if existing is None:
            catalog.insert_registry(registry)
        elif registry != existing:
            raise ValueError('registry_conflict')
    return registry.version
