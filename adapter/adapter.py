from __future__ import annotations
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from base import Record, Input


class Adapter(ABC):
    input_types: type[Input] | list[type[Input]]
    output_types: type[Record] | list[type[Record]]

    def __init__(self, name: str, run_id: str, timestep_length: timedelta, **kwargs):
        self.name = name
        self.run_id = run_id
        self._timestep_length = timestep_length
        # Set by the AdapterWorker before initialize().
        # When the model's first step starts, measured from the start of the
        # run (ModelSpec.active_from).
        self.active_from = timedelta(0)
        # The calendar time of the run's start, or None if the run has none.
        self.start_time: datetime | None = None

    @property
    def timestep_length(self) -> timedelta:
        """Return the model's timestep length."""
        return self._timestep_length

    @abstractmethod
    def current_time(self) -> timedelta:
        """Return the model's current simulation time, measured from the start of the run.

        For example, a model active from two hours into the run reports
        timedelta(hours=3) after advancing one hour.
        """
        pass

    def to_run_time(self, time: datetime) -> timedelta:
        """Convert a calendar time to run time, e.g. in current_time() of a
        model whose clock is a date."""
        if self.start_time is None:
            raise RuntimeError(
                f"{type(self).__name__} converts calendar time {time} to run "
                f"time, but the run has no start_time; pass one to "
                f"SupervisoryModel"
            )
        if (time.tzinfo is None) != (self.start_time.tzinfo is None):
            raise TypeError(
                f"{type(self).__name__} converts calendar time {time} to run "
                f"time, but only one of it and the run's start_time "
                f"{self.start_time} has a timezone; give both one, or neither"
            )
        if time.tzinfo is None:
            return time - self.start_time
        # Python subtracts datetimes that share a timezone by wall clock,
        # which skips or adds an hour across a daylight saving change.
        return time.astimezone(timezone.utc) - self.start_time.astimezone(timezone.utc)

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
