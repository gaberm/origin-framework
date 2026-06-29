from __future__ import annotations
from abc import ABC, abstractmethod
from base import Record, Input


class Adapter(ABC):
    input_types: type[Input] | list[type[Input]]
    output_types: type[Record] | list[type[Record]]
    constant_types: type[Record] | list[type[Record]] | None = None

    def __init__(self, name: str, timestep_length: float, **kwargs):
        self.name = name
        self.timestep_length = timestep_length
        self.model_time = 0.0

    @property
    def timestep_length(self) -> float:
        """Return the model's timestep length in global time units."""
        return self.timestep_length

    @abstractmethod
    def initialize(self):
        """Initialize the model."""
        pass

    def read_constants(self) -> list[Record]:
        """Read the model's time-invariant records."""
        return []

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
    def advance(self) -> float:
        """Advance the model by dt global time units. Returns the new model time."""
        pass

    @abstractmethod
    def terminate(self):
        """Terminate the model."""
        pass
