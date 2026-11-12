from dataclasses import dataclass

# Seconds a reported model time may differ from the expected window end.
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
                f"TimeWindow.start must be non-negative; got {self.start_s}"
            )
        if self.start_s >= self.end_s:
            raise ValueError(
                f"TimeWindow.start must be less than end; "
                f"got start={self.start_s}, end={self.end_s}"
            )
