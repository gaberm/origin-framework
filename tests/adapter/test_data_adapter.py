import pytest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from adapter import DataAdapter
from base import Record


@dataclass(kw_only=True)
class Station(Record):
    primary_key = ("station_id",)

    station_id: str
    port_count: int
    power_kw: float | None


class MinimalDataAdapter(DataAdapter):
    output_types = [Station]

    def load_data(self):
        return []


def data_adapter(start_time):
    adapter = MinimalDataAdapter(name="data")
    adapter.start_time = start_time
    return adapter


def test_load_data_is_required():
    class NoLoadData(DataAdapter):
        output_types = []

    with pytest.raises(TypeError, match="load_data"):
        NoLoadData(name="data")


class TestTimesInRun:
    def test_skips_times_before_the_run_start(self):
        adapter = data_adapter(datetime(2026, 1, 18, 18, tzinfo=timezone.utc))
        times = [
            datetime(2026, 1, 18, hour, tzinfo=timezone.utc) for hour in (17, 18, 19)
        ]
        with pytest.warns(
            UserWarning,
            match=r"skipped 1 times before the run's `start_time` "
            r"2026-01-18 18:00:00\+00:00, the latest 2026-01-18 17:00:00\+00:00$",
        ):
            assert list(adapter.times_in_run(times)) == [
                (1, timedelta(0)),
                (2, timedelta(hours=1)),
            ]

    def test_warns_of_latest_skipped_time_when_unordered(self):
        adapter = data_adapter(datetime(2026, 1, 18, 18, tzinfo=timezone.utc))
        times = [
            datetime(2026, 1, 18, hour, tzinfo=timezone.utc) for hour in (16, 18, 17)
        ]
        with pytest.warns(UserWarning, match=r"the latest 2026-01-18 17:00:00"):
            list(adapter.times_in_run(times))

    def test_does_not_warn_when_no_time_is_skipped(self, recwarn):
        adapter = data_adapter(datetime(2026, 1, 18, 18, tzinfo=timezone.utc))
        times = [datetime(2026, 1, 18, 18, tzinfo=timezone.utc)]
        assert list(adapter.times_in_run(times)) == [(0, timedelta(0))]
        assert not recwarn.list

    def test_rejects_all_times_before_the_run_start(self):
        adapter = data_adapter(datetime(2026, 1, 18, 19, tzinfo=timezone.utc))
        times = [datetime(2026, 1, 18, hour, tzinfo=timezone.utc) for hour in (17, 18)]
        with pytest.raises(
            ValueError,
            match=r"has no times at or after the run's `start_time` "
            r"2026-01-18 19:00:00\+00:00; the latest is 2026-01-18 18:00:00\+00:00$",
        ):
            list(adapter.times_in_run(times))

    def test_accepts_no_times(self):
        adapter = data_adapter(datetime(2026, 1, 18, 18, tzinfo=timezone.utc))
        assert list(adapter.times_in_run([])) == []

    def test_converts_across_timezones(self):
        adapter = data_adapter(
            datetime(2026, 1, 18, 12, tzinfo=timezone(timedelta(hours=-6)))
        )
        times = [datetime(2026, 1, 18, 19, tzinfo=timezone.utc)]
        assert list(adapter.times_in_run(times)) == [(0, timedelta(hours=1))]

    def test_rejects_run_without_start_time(self):
        adapter = data_adapter(None)
        with pytest.raises(
            RuntimeError, match="This requires the run to have a `start_time`"
        ):
            list(adapter.times_in_run([datetime(2026, 1, 18)]))


class TestLoadCsv:
    def test_one_record_per_row(self, tmp_path):
        csv_path = tmp_path / "stations.csv"
        csv_path.write_text("station_id,port_count,power_kw\n007,2,150.0\n008,4,\n")
        stations = data_adapter(None).load_csv(str(csv_path), Station)
        assert stations == [
            Station(station_id="007", port_count=2, power_kw=150.0),
            Station(station_id="008", port_count=4, power_kw=None),
        ]
        assert type(stations[0].port_count) is int

    def test_rejects_unknown_column(self, tmp_path):
        csv_path = tmp_path / "stations.csv"
        csv_path.write_text("station_id,port_count,power_kw,owner\n007,2,150.0,city\n")
        with pytest.raises(TypeError, match="owner"):
            data_adapter(None).load_csv(str(csv_path), Station)
