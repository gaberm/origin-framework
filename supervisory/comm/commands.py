from enum import Enum
from typing import Any
from dataclasses import dataclass


class Operation(str, Enum):
    INITIALIZE = "initialize"
    READ_CONSTANTS = "read_constants"
    WRITE_INPUTS = "write_inputs"
    READ_OUTPUTS = "read_outputs"
    ADVANCE = "advance"
    TERMINATE = "terminate"


@dataclass
class Message:
    command: Operation
    payload: Any = None

    def to_dict(self) -> dict:
        return {
            "command": self.command.value,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Message":
        return cls(command=Operation(d["command"]), payload=d.get("payload"))


@dataclass
class Response:
    success: bool
    payload: Any = None
    error: str = None

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "payload": self.payload,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Response":
        return cls(success=d["success"], payload=d.get("payload"), error=d.get("error"))


@dataclass
class Registration:
    name: str
    routing_key: str
    metadata: dict | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "routing_key": self.routing_key,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Registration":
        return cls(
            name=d["name"], routing_key=d["routing_key"], metadata=d.get("metadata")
        )
