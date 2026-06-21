from dataclasses import dataclass


@dataclass(kw_only=True)
class Timestamp:
    time: float

    def _validate_timestamp(self):
        if self.time < 0:
            raise ValueError("Timestamp must be non-negative.")
