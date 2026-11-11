from __future__ import annotations
from abc import ABC, abstractmethod
from datetime import datetime
from base import Record
from .simulation_clock import SimulationClock


class DataAdapter(SimulationClock, ABC):
    """Connects a data source, e.g. a file, to the supervisor.

    The data is loaded once, before the simulation starts.

    `output_types` are the records `load_data` returns.
    """

    output_types: type[Record] | list[type[Record]]

    def __init__(self, name: str, **kwargs):
        self.name = name
        self.start_time: datetime | None = None

    @abstractmethod
    def load_data(self) -> list[Record]:
        """Load all records of the data source."""
        pass
