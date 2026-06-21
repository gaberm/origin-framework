from abc import ABC, abstractmethod
from base import Record
from base import Input


class Adapter(ABC):
    InputType: type[Input] | list[type[Input]]
    OutputType: type[Record] | list[type[Record]]
    ConstantType: type[Record] | list[type[Record]] | None = None

    def __init__(self, name: str, timestep_length: float, **kwargs):
        self.name = name
        self._timestep_length = timestep_length
        self._model_time = 0.0

    @property
    def model_time(self) -> float:
        """Return the model's current simulation time in global time units."""
        return self._model_time

    @property
    def timestep_length(self) -> float:
        """Return the model's timestep length in global time units."""
        return self._timestep_length

    @abstractmethod
    def initialize(self):
        """Initialize the model."""
        pass

    def read_constants(self) -> list[Record]:
        """Read the model's time-invariant records."""
        return []

    @abstractmethod
    def read_outputs(self) -> list[type[Record]]:
        """Read the user-defined outputs of the model."""
        pass

    @abstractmethod
    def write_inputs(self, inputs: dict[str, list[dict]]):
        """Write user-defined inputs to the model.

        `inputs` is keyed by each inputs `key` (its class name by default)
        """
        pass

    @abstractmethod
    def advance(self):
        """Advance the model by dt global time units."""
        pass

    @abstractmethod
    def terminate(self):
        """Terminate the model."""
        pass
