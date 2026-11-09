from __future__ import annotations
import warnings
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import fields
from datetime import datetime, timedelta
from typing import TypeVar, get_type_hints
import pandas as pd
from base import Record
from .simulation_clock import SimulationClock

RecordType = TypeVar("RecordType", bound=Record)


class DataAdapter(SimulationClock, ABC):
    """Wraps a data source, e.g. a file, that is loaded once instead of
    stepped like a model.

    `output_types` are the record types load_data returns."""

    output_types: type[Record] | list[type[Record]]

    def __init__(self, name: str, **kwargs):
        self.name = name
        self.start_time: datetime | None = None

    @abstractmethod
    def load_data(self) -> list[Record]:
        """Load all records of the data source."""
        pass

    def times_in_run(self, times: Iterable[datetime]) -> list[tuple[int, timedelta]]:
        """The index and simulation time of each physical time at or after the
        run's start. Earlier times are skipped with a warning, and if all of
        them are, which usually means a wrong `start_time`, it raises."""
        times = list(times)
        simulation_times = [self.to_simulation_time(time) for time in times]
        in_run = [
            (index, simulation_time)
            for index, simulation_time in enumerate(simulation_times)
            if simulation_time >= timedelta(0)
        ]
        skipped = [
            time
            for time, simulation_time in zip(times, simulation_times)
            if simulation_time < timedelta(0)
        ]
        if skipped and not in_run:
            raise ValueError(
                f"{type(self).__name__} has no times at or after the run's "
                f"`start_time` {self.start_time}; the latest is {max(skipped)}"
            )
        if skipped:
            warnings.warn(
                f"{type(self).__name__} skipped {len(skipped)} times before the "
                f"run's `start_time` {self.start_time}, the latest {max(skipped)}"
            )
        return in_run

    def load_csv(
        self, file_path: str, output_type: type[RecordType]
    ) -> list[RecordType]:
        """Load one record per row of a CSV file whose columns are named
        after the record's fields. Empty cells become None, and columns of
        `str` fields stay text, so an id like "007" keeps its zeros."""
        type_hints = get_type_hints(output_type)
        text_columns = {
            field.name: str
            for field in fields(output_type)
            if str in _member_types(type_hints.get(field.name))
        }
        frame = pd.read_csv(file_path, dtype=text_columns)
        rows = frame.astype(object).where(frame.notna(), None).to_dict("records")
        return [output_type(**row) for row in rows]


def _member_types(type_hint) -> tuple:
    """The types a hint allows, e.g. (str, NoneType) for `str | None`."""
    return getattr(type_hint, "__args__", (type_hint,))
