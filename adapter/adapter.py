from __future__ import annotations
from abc import ABC, abstractmethod
from datetime import timedelta
from base import Record, Input


class Adapter(ABC):
    input_types: type[Input] | list[type[Input]]
    output_types: type[Record] | list[type[Record]]

    def __init__(self, name: str, run_id: str, timestep_length: timedelta, **kwargs):
        self.name = name
        self.run_id = run_id
        self._timestep_length = timestep_length

    @property
    def timestep_length(self) -> timedelta:
        """Return the model's timestep length."""
        return self._timestep_length

    @abstractmethod
    def current_time(self) -> timedelta:
        """Return the model's current time, measured from the start of the run."""
        pass

    @abstractmethod
    def initialize(self) -> list[Record] | None:
        """Initialize the model.

        May return records of `initialize_only` entities; any others are rejected.
        """
        pass

    @abstractmethod
    def read_outputs(self) -> list[Record]:
        """Read the user-defined outputs of the model."""
        pass

    @abstractmethod
    def write_inputs(self, inputs: dict[str, list[dict]]):
        """Write user-defined inputs to the model.

        `inputs` is keyed by each inputs `key` (its class name by default)
        """
        pass

    @abstractmethod
    def advance(self) -> None:
        """Advance the model by one timestep (timestep_length)."""
        pass

    @abstractmethod
    def terminate(self) -> list[Record] | None:
        """Terminate the model.

        May return final user-defined outputs, as `read_outputs` does.
        """
        pass
