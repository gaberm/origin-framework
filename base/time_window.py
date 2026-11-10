from dataclasses import dataclass

# Seconds a reported model time may differ from the expected window end.
TIME_TOLERANCE = 1e-6


@dataclass(frozen=True)
class TimeWindow:
    start: float
    end: float

    def __post_init__(self):
        if self.start < 0:
            raise ValueError("start must be non-negative")
        if self.start >= self.end:
            raise ValueError("start must be less than end")
