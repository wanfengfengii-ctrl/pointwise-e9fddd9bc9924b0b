from dataclasses import dataclass

from warehouse.catalog.partitions import require_identifier, require_partition


@dataclass(frozen=True)
class SourceRegistration:
    source: str
    partition: str


def index_sources(registrations, partitions):
    sources = {}
    for registration in registrations:
        if not isinstance(registration, SourceRegistration):
            raise ValueError('invalid_source_registration')
        source = require_identifier(registration.source, 'source')
        require_partition(partitions, registration.partition)
        if source in sources:
            raise ValueError('duplicate_source')
        sources[source] = registration
    return sources


def require_source_partition(sources, source: str, partition: str) -> SourceRegistration:
    require_identifier(source, 'source')
    registration = sources.get(source)
    if registration is None:
        raise ValueError('unknown_source')
    if registration.partition != partition:
        raise ValueError('source_partition_mismatch')
    return registration
