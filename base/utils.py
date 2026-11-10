import dataclasses
import re
from typing import Any
from base.output.record import Record


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, (list, tuple)) else [value]


def validate_run_id(run_id: str) -> None:
    """A run's id names its PostgreSQL schema and its RabbitMQ queues."""
    if not isinstance(run_id, str):
        raise TypeError(f"run_id must be a str; got {type(run_id).__name__!r}")
    if not re.match(r"^[a-z_][a-z0-9_]{0,62}$", run_id):
        raise ValueError(
            f"run_id '{run_id}' is not a valid PostgreSQL schema name. "
            f"Use lowercase letters, digits, and underscores only, "
            f"start with a letter or underscore, and keep it to at most 63 characters."
        )


def records_to_dicts(records: list[Record]) -> list[dict]:
    """Records as dicts for the broker, each tagged with its `_type`."""
    return [
        {"_type": type(record).__name__, **dataclasses.asdict(record)}
        for record in records
    ]


def dicts_to_records(
    record_dicts: list[dict], record_types: type[Record] | list[type[Record]]
) -> list[Record]:
    """Rebuild records from records_to_dicts output. Only the declared
    `record_types` are accepted."""
    types_by_name = {
        record_type.__name__: record_type for record_type in as_list(record_types)
    }
    records = []
    for record_dict in record_dicts:
        fields = dict(record_dict)
        type_name = fields.pop("_type")
        if type_name not in types_by_name:
            raise ValueError(
                f"received a {type_name} record, but the model declares "
                f"{sorted(types_by_name)}"
            )
        records.append(types_by_name[type_name](**fields))
    return records
