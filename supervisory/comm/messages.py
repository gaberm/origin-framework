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
    def from_dict(cls, data: dict) -> "Message":
        return cls(command=Operation(data["command"]), payload=data.get("payload"))


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
    def from_dict(cls, data: dict) -> "Response":
        return cls(
            success=data["success"],
            payload=data.get("payload"),
            error=data.get("error"),
        )


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
    def from_dict(cls, data: dict) -> "Registration":
        return cls(
            name=data["name"],
            routing_key=data["routing_key"],
            metadata=data.get("metadata"),
        )
