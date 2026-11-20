from dataclasses import dataclass


def require_identifier(value: str, field: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f'invalid_{field}')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError as error:
        raise ValueError(f'invalid_{field}') from error
    return value


@dataclass(frozen=True)
class PartitionDefinition:
    name: str


def index_partitions(definitions):
    partitions = {}
    for definition in definitions:
        if not isinstance(definition, PartitionDefinition):
            raise ValueError('invalid_partition_definition')
        name = require_identifier(definition.name, 'partition')
        if name in partitions:
            raise ValueError('duplicate_partition')
        partitions[name] = definition
    if not partitions:
        raise ValueError('empty_partitions')
    return partitions


def require_partition(partitions, name: str) -> PartitionDefinition:
    require_identifier(name, 'partition')
    if name not in partitions:
        raise ValueError('unknown_partition')
    return partitions[name]
