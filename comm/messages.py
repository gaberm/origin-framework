import dataclasses
from enum import Enum
from typing import Any
from dataclasses import dataclass
from base import Record, as_list


def task_exchange(run_id: str) -> str:
    return f"{run_id}.tasks"


def registration_queue(run_id: str) -> str:
    return f"{run_id}.worker_registration"


def task_queue(run_id: str, model_name: str) -> str:
    return f"{run_id}.{model_name}_queue"


class Command(str, Enum):
    INITIALIZE = "initialize"
    WRITE_INPUTS = "write_inputs"
    READ_OUTPUTS = "read_outputs"
    ADVANCE = "advance"
    TERMINATE = "terminate"


@dataclass
class Request:
    """Sent from the supervisor to a worker to run a command."""

    command: Command
    payload: Any = None

    def to_dict(self) -> dict:
        return {
            "command": self.command.value,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Request":
        return cls(command=Command(data["command"]), payload=data.get("payload"))


@dataclass
class Response:
    """Sent from a worker to the supervisor as a reply to a Request, or from
    the supervisor to a worker as a reply to a Registration."""

    success: bool
    payload: Any = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "payload": self.payload,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Response":
        return cls(
            success=data["success"],
            payload=data.get("payload"),
            error=data.get("error"),
        )


@dataclass
class Registration:
    """Sent from a worker to the supervisor to request registration. The
    step_schedule must match the supervisor's ModelSpec, or registration is
    rejected."""

    model_name: str
    routing_key: str
    step_schedule: dict | None = None

    def to_dict(self) -> dict:
        return {
            "model_name": self.model_name,
            "routing_key": self.routing_key,
            "step_schedule": self.step_schedule,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Registration":
        return cls(
            model_name=data["model_name"],
            routing_key=data["routing_key"],
            step_schedule=data.get("step_schedule"),
        )


def records_to_payloads(records: list[Record]) -> list[dict]:
    """Convert records to dicts for transport. Each dict gets a `_type` field
    that lets the supervisor rebuild the record with payloads_to_records.

    Example:
        >>> records_to_payloads([Vehicle(veh_id="veh_1", soc=0.5, time_s=1.0)])
        [{'_type': 'Vehicle', 'veh_id': 'veh_1', 'soc': 0.5, 'time_s': 1.0}]
    """
    return [
        {"_type": type(record).__name__, **dataclasses.asdict(record)}
        for record in records
    ]


def payloads_to_records(
    payloads: list[dict], record_types: type[Record] | list[type[Record]]
) -> list[Record]:
    """Convert dicts from records_to_payloads back to records. Each dict's
    `_type` field must name one of `record_types`.

    Example:
        >>> payloads_to_records(
        ...     [{"_type": "Vehicle", "veh_id": "veh_1", "soc": 0.5, "time_s": 1.0}],
        ...     [Vehicle, Battery],
        ... )
        [Vehicle(veh_id='veh_1', soc=0.5, time_s=1.0)]
    """
    types_by_name = {
        record_type.__name__: record_type for record_type in as_list(record_types)
    }
    records = []
    for payload in payloads:
        fields = dict(payload)
        type_name = fields.pop("_type")
        if type_name not in types_by_name:
            raise ValueError(
                f"received a {type_name} record, but the model declares "
                f"{sorted(types_by_name)}"
            )
        records.append(types_by_name[type_name](**fields))
    return records
