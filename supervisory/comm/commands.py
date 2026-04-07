from enum import Enum
from typing import Any
from dataclasses import dataclass


class Operation(str, Enum):
    INITIALIZE = "initialize"
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
