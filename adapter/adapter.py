from __future__ import annotations
from abc import ABC, abstractmethod
from datetime import timedelta
from base import Record, Input


class Adapter(ABC):
    input_types: type[Input] | list[type[Input]]
    output_types: type[Record] | list[type[Record]]
    constant_types: type[Record] | list[type[Record]] | None = None

    def __init__(self, name: str, timestep_length: timedelta, **kwargs):
        if not isinstance(timestep_length, timedelta):
            raise TypeError(
                f"{type(self).__name__}: timestep_length must be a "
                f"datetime.timedelta; got {type(timestep_length).__name__!r}"
            )
        if not isinstance(name, str):
            raise TypeError(
                f"{type(self).__name__}: name must be a "
                f"str; got {type(name).__name__!r}"
            )
        self.name = name
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
    def advance(self) -> None:
        """Advance the model by one timestep (timestep_length)."""
        pass

    @abstractmethod
    def terminate(self) -> list[Record] | None:
        """Terminate the model.

        May return final outputs, e.g. state that only ends with the run. They
        are stored like the outputs of `read_outputs`."""
        pass
