from __future__ import annotations
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from base import Record, Input
from .simulation_clock import SimulationClock


class Adapter(SimulationClock, ABC):
    """Connects a model to the supervisor. One Adapter per model.

    `input_types` are the inputs the model reads.

    `output_types` are the records the model writes.
    """

    input_types: type[Input] | list[type[Input]]
    output_types: type[Record] | list[type[Record]]

    def __init__(
        self,
        name: str,
        run_id: str,
        timestep_length: timedelta,
        active_from: timedelta,
        **kwargs,
    ):
        self.name = name
        self.run_id = run_id
        self.timestep_length = timestep_length
        self.active_from = active_from
        self.start_time: datetime | None = None

    @abstractmethod
    def current_time(self) -> timedelta:
        """Return the model's current simulation time, measured from the start of the run.

        For example, a model active from two hours into the run reports
        timedelta(hours=3) after advancing one hour.
        """
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

        `inputs` is keyed by each input's `name`, e.g.
        {"EV": [{"veh_id": 1, "soc": 0.5}, ...]} for Input(name="EV", ...).
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
