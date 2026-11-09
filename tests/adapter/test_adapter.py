import pytest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from adapter.adapter import Adapter


class MinimalAdapter(Adapter):
    input_types = []
    output_types = []

    def current_time(self): ...
    def initialize(self): ...
    def read_outputs(self): ...
    def write_inputs(self, inputs): ...
    def advance(self): ...
    def terminate(self): ...


def adapter(start_time):
    adapter = MinimalAdapter(
        name="model", run_id="run_test", timestep_length=timedelta(hours=1)
    )
    adapter.start_time = start_time
    return adapter


class TestToSimulationTime:
    def test_without_timezones(self):
        model = adapter(datetime(2026, 1, 18))
        assert model.to_simulation_time(datetime(2026, 1, 18, 6)) == timedelta(hours=6)

    def test_counts_real_time_across_daylight_saving_change(self):
        chicago = ZoneInfo("America/Chicago")
        model = adapter(datetime(2026, 3, 8, tzinfo=chicago))
        # Clocks skip from 02:00 to 03:00, so only 3 hours pass until 04:00.
        assert model.to_simulation_time(
            datetime(2026, 3, 8, 4, tzinfo=chicago)
        ) == timedelta(hours=3)

    def test_across_timezones(self):
        model = adapter(datetime(2026, 1, 18, 18, tzinfo=timezone.utc))
        central = timezone(timedelta(hours=-6))
        assert model.to_simulation_time(
            datetime(2026, 1, 18, 13, tzinfo=central)
        ) == timedelta(hours=1)

    def test_rejects_one_timezone_missing(self):
        model = adapter(datetime(2026, 1, 18, tzinfo=timezone.utc))
        with pytest.raises(TypeError, match="to have a timezone, or neither$"):
            model.to_simulation_time(datetime(2026, 1, 18, 6))

    def test_rejects_run_without_start_time(self):
        model = adapter(None)
        with pytest.raises(
            RuntimeError, match="This requires the run to have a `start_time`"
        ):
            model.to_simulation_time(datetime(2026, 1, 18, 6))
