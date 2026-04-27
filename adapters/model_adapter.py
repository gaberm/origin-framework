from abc import ABC, abstractmethod
from dataclasses import dataclass
from literalai import Optional


class ModelAdapter(ABC):
    InputType: type[dataclass] = None
    OutputType: type[dataclass] = None
    _registry: dict[str, type["ModelAdapter"]] = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        ModelAdapter._registry[cls.__name__] = cls

    def __init__(self, name: str, timestep_length: Optional[float] = None):
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
        if self._timestep_length is None:
            raise ValueError("timestep_length not set.")
        return self._timestep_length

    @abstractmethod
    def initialize(self):
        """Initialize the model."""
        pass

    @abstractmethod
    def read_outputs(self) -> dataclass:
        pass

    @abstractmethod
    def write_inputs(self, inputs: dataclass):
        pass

    @abstractmethod
    def advance(self, dt: float):
        """Advance the model by dt global time units."""
        pass
