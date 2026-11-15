from __future__ import annotations
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from base import Record, Input
from .simulation_clock import SimulationClock


class Adapter(SimulationClock, ABC):
    """Connects a model to the supervisor. One Adapter per model.

    `input_types` are the inputs the model reads.

    `output_types` are the records the model writes.

    `model_name`, `timestep_length` and `active_from` are those of the
    model's ModelSpec. `run_id` is the run the model takes part in.

    A subclass takes its ModelSpec `params` as arguments of its own __init__.

    `start_time` is the run's start as a physical time, or None if the run
    has none. It is set after __init__ and before `initialize`.
    """

    input_types: type[Input] | list[type[Input]]
    output_types: type[Record] | list[type[Record]]

    def __init__(
        self,
        model_name: str,
        run_id: str,
        timestep_length: timedelta,
        active_from: timedelta,
    ):
        self.name = model_name
        self.run_id = run_id
        self.timestep_length = timestep_length
        self.active_from = active_from
        self.start_time: datetime | None = None

    @abstractmethod
    def current_time(self) -> timedelta:
        """Return the model's current simulation time, i.e. the time since the run
        started, not since the model's own first step.

        For example, a model with active_from=timedelta(hours=2) reports
        timedelta(hours=3) after advancing one hour.
        """
        pass

    @abstractmethod
    def initialize(self) -> list[Record] | None:
        """Initialize the model.

        The model must be at `self.active_from` after initialize, i.e.
        `current_time()` must return the same time. Otherwise the run fails
        before the first step.

        May return outputs, e.g. infrastructure that exists for the whole
        run. They are stored before the first step.
        """
        pass

    @abstractmethod
    def read_outputs(self) -> list[Record]:
        """Return the model outputs from the last step.

        May return only some or none of the output types.
        """
        pass

    @abstractmethod
    def write_inputs(self, inputs: dict[str, list[dict]]):
        """Write the inputs for the next step to the model.

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
