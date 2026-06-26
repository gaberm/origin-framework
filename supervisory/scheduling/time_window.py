from dataclasses import dataclass


@dataclass(frozen=True)
class TimeWindow:
    start: float
    end: float

    def __post_init__(self):
        if self.start < 0:
            raise ValueError("start must be non-negative")
        if self.start >= self.end:
            raise ValueError("start must be less than end")
