from datetime import datetime, timedelta, timezone


class SimulationClock:
    """Converts physical times to simulation time, measured from the run's
    `start_time`."""

    start_time: datetime | None = None

    def to_simulation_time(self, time: datetime) -> timedelta:
        """For example, datetime(1794, 9, 10, 6) is timedelta(hours=6) in a run with
        start_time datetime(1794, 9, 10)."""

        if self.start_time is None:
            raise RuntimeError(
                f"{type(self).__name__} converts physical time {time} to simulation "
                f"time. This requires the run to have a `start_time`; pass one to "
                f"SupervisoryModel"
            )
        if (time.tzinfo is None) != (self.start_time.tzinfo is None):
            raise TypeError(
                f"{type(self).__name__} converts physical time {time} to simulation "
                f"time. This requires both it and the run's `start_time` "
                f"{self.start_time} to have a timezone, or neither"
            )
        if time.tzinfo is None:
            return time - self.start_time
        # Python subtracts datetimes that share a timezone by wall clock,
        # which skips or adds an hour across a daylight saving change.
        return time.astimezone(timezone.utc) - self.start_time.astimezone(timezone.utc)
