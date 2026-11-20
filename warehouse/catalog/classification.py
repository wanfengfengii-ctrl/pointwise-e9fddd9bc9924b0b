from dataclasses import dataclass

from warehouse.catalog.partitions import require_identifier, require_partition


@dataclass(frozen=True)
class Classification:
    partition: str
    category: str


def index_classifications(registrations, partitions):
    classifications = {}
    for registration in registrations:
        if not isinstance(registration, Classification):
            raise ValueError('invalid_classification')
        require_partition(partitions, registration.partition)
        require_identifier(registration.category, 'category')
        key = (registration.partition, registration.category)
        if key in classifications:
            raise ValueError('duplicate_classification')
        classifications[key] = registration
    return classifications


def require_partition_category(classifications, partition: str, category: str) -> Classification:
    require_identifier(category, 'category')
    key = (partition, category)
    if key not in classifications:
        raise ValueError('unknown_partition_category')
    return classifications[key]
