from dataclasses import dataclass

# Seconds two simulation times may differ by and still count as equal
TIME_TOLERANCE = 1e-6


@dataclass(frozen=True)
class TimeWindow:
    """The span of simulation time [start, end), in seconds, that a model's
    inputs are read for."""

    start_s: float
    end_s: float

    def __post_init__(self):
        if self.start_s < 0:
            raise ValueError(
                f"TimeWindow.start_s must be non-negative; got {self.start_s}"
            )
        if self.start_s >= self.end_s:
            raise ValueError(
                f"TimeWindow.start_s must be less than TimeWindow.end_s; "
                f"got start_s={self.start_s}, end_s={self.end_s}"
            )


@dataclass(frozen=True)
class LateRecords:
    """The records from before a model's time window that it has not read
    yet, because they were written after it read the window they belong to.

    `start_s` is the earliest time such a record can have.

    `written_after_s` is the simulation time their producer had reached when
    the model last read its inputs; records written later are new to it.
    """

    start_s: float
    written_after_s: float
