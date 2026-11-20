from warehouse.catalog.bootstrap import initialize_registry
from warehouse.catalog.classification import Classification
from warehouse.catalog.partitions import PartitionDefinition
from warehouse.catalog.sources import SourceRegistration


def registry_entries():
    return (
        (PartitionDefinition('A'), PartitionDefinition('B')),
        (SourceRegistration('meter-A', 'A'), SourceRegistration('meter-B', 'B')),
        (Classification('A', 'cooling'), Classification('A', 'storm'),
         Classification('B', 'cooling'), Classification('B', 'effluent')),
    )


def initialize_source(connection):
    return initialize_registry(connection, *registry_entries())
