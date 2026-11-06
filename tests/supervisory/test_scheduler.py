import pytest
from datetime import timedelta
from dataclasses import dataclass
from base import Entity, Record, Input, Fields, ModelSpec, KeyJoin, Latest
from adapter.adapter import Adapter
from supervisory.scheduling.scheduler import Scheduler
from supervisory.scheduling.time_window import TimeWindow


@dataclass(kw_only=True)
class RecordA(Record):
    table_name = "record_a"
    primary_key = ("id",)
    id: str


@dataclass(kw_only=True)
class RecordB(Record):
    table_name = "record_b"
    primary_key = ("id",)
    id: str


@dataclass(kw_only=True)
class RecordC(Record):
    table_name = "record_c"
    primary_key = ("id",)
    id: str


@dataclass(kw_only=True)
class RecordD(Record):
    table_name = "record_d"
    primary_key = ("id",)
    id: str


class _Base(Adapter):
    input_types = []
    output_types = []

    def initialize(self):
        pass

    def read_outputs(self):
        return []

    def write_inputs(self, inputs):
        pass

    def advance(self):
        pass

    def terminate(self):
        pass


class AdapterA(_Base):
    input_types = []
    output_types = RecordA


class AdapterB(_Base):
    input_types = Input(name="record_a", from_=RecordA, select=Fields("id"))
    output_types = RecordB


class AdapterBLatest(_Base):
    input_types = Input(
        name="record_a", from_=RecordA, select=Fields("id"), read_policy=Latest(by="id")
    )
    output_types = RecordB


def spec(name, adapter, dt=1.0, dependencies=None, **active_window):
    return ModelSpec(
        name=name,
        adapter=adapter,
        timestep_length=timedelta(seconds=dt),
        dependencies=dependencies,
        **active_window,
    )


class TestSingleModel:
    class AdapterA(_Base):
        input_types = []
        output_types = RecordA

    def test_select_returns_model(self):
        scheduler = Scheduler([spec("model_a", self.AdapterA)], duration=timedelta(seconds=2.0))
        assert scheduler.select_next() == "model_a"

    def test_select_returns_none_when_done(self):
        scheduler = Scheduler([spec("model_a", self.AdapterA)], duration=timedelta(seconds=1.0))
        scheduler.sync_model_time("model_a", 1.0)
        assert scheduler.select_next() is None

    def test_time_window_is_correct(self):
        scheduler = Scheduler([spec("model_a", self.AdapterA)], duration=timedelta(seconds=1.0))
        assert scheduler.time_window("model_a") == TimeWindow(0.0, 1.0)

    def test_sync_time_updates_model_time(self):
        scheduler = Scheduler([spec("model_a", self.AdapterA)], duration=timedelta(seconds=2.0))
        scheduler.sync_model_time("model_a", 1.0)
        assert scheduler.model_times["model_a"] == 1.0


class TestTwoModelChain:
    class AdapterA(_Base):
        input_types = []
        output_types = RecordA

    class AdapterB(_Base):
        input_types = Input(name="record_a", from_=RecordA, select=Fields("id"))
        output_types = RecordB

    class AdapterBLatest(_Base):
        input_types = Input(
            name="record_a",
            from_=RecordA,
            select=Fields("id"),
            read_policy=Latest(by="id"),
        )
        output_types = RecordB

    def test_select_returns_model(self):
        scheduler = Scheduler(
            [spec("model_a", self.AdapterA), spec("model_b", self.AdapterB)],
            duration=timedelta(seconds=2.0),
        )
        assert scheduler.select_next() == "model_a"
        scheduler.sync_model_time("model_a", 1.0)
        assert scheduler.select_next() == "model_b"

    def test_latest_does_not_block(self):
        scheduler = Scheduler(
            [spec("model_a", self.AdapterA), spec("model_b", self.AdapterBLatest)],
            duration=timedelta(seconds=2.0),
        )
        assert scheduler.dependencies["model_b"] == ()


@dataclass(kw_only=True)
class Depot(Entity):
    id_field = "depot_id"
    initialize_only = True

    depot_id: str


class TestInitializeOnlyOutputs:
    class AdapterA(_Base):
        input_types = []
        output_types = [RecordA, Depot]

    class AdapterB(_Base):
        input_types = Input(name="depot", from_=Depot, select=Fields("depot_id"))
        output_types = RecordB

    def test_reading_them_does_not_block(self):
        scheduler = Scheduler(
            [spec("model_a", self.AdapterA), spec("model_b", self.AdapterB)],
            duration=timedelta(seconds=2.0),
        )
        assert scheduler.dependencies["model_b"] == ()


class TestCircularDependency:
    class AdapterA(_Base):
        input_types = Input(name="record_b", from_=RecordB, select=Fields("id"))
        output_types = RecordA

    class AdapterB(_Base):
        input_types = Input(name="record_a", from_=RecordA, select=Fields("id"))
        output_types = RecordB

    def test_raises(self):
        with pytest.raises(RuntimeError, match="coupling cycle"):
            scheduler = Scheduler(
                [spec("a", self.AdapterA), spec("b", self.AdapterB)],
                duration=timedelta(seconds=2.0),
            )
            scheduler.select_next()


class TestTripleModel:
    class AdapterA(_Base):
        input_types = []
        output_types = RecordA

    class AdapterB(_Base):
        input_types = Input(name="record_a", from_=RecordA, select=Fields("id"))
        output_types = RecordB

    class AdapterC(_Base):
        input_types = Input(name="record_b", from_=RecordB, select=Fields("id"))
        output_types = RecordC

    def test_select_returns_model(self):
        scheduler = Scheduler(
            [
                spec("model_a", self.AdapterA),
                spec("model_b", self.AdapterB),
                spec("model_c", self.AdapterC),
            ],
            duration=timedelta(seconds=2.0),
        )
        assert scheduler.select_next() == "model_a"
        scheduler.sync_model_time("model_a", 1.0)
        assert scheduler.select_next() == "model_b"
        scheduler.sync_model_time("model_b", 1.0)
        assert scheduler.select_next() == "model_c"


class TestQuadrupleModel:
    class AdapterA(_Base):
        input_types = []
        output_types = RecordA

    class AdapterB(_Base):
        input_types = Input(name="record_a", from_=RecordA, select=Fields("id"))
        output_types = RecordB

    class AdapterC(_Base):
        input_types = Input(name="record_a", from_=RecordA, select=Fields("id"))
        output_types = RecordC

    class AdapterD(_Base):
        input_types = Input(
            name="record_b_c",
            from_=RecordB,
            on=KeyJoin((RecordB, "id"), (RecordC, "id")),
            select=Fields((RecordB, "id"), (RecordC, "id")),
        )
        output_types = RecordD

    def test_select_returns_model(self):
        scheduler = Scheduler(
            [
                spec("model_a", self.AdapterA),
                spec("model_b", self.AdapterB),
                spec("model_c", self.AdapterC),
                spec("model_d", self.AdapterD),
            ],
            duration=timedelta(seconds=2.0),
        )
        assert scheduler.select_next() == "model_a"
        scheduler.sync_model_time("model_a", 1.0)
        assert scheduler.select_next() == "model_b"
        scheduler.sync_model_time("model_b", 1.0)
        assert scheduler.select_next() == "model_c"
        scheduler.sync_model_time("model_c", 1.0)
        assert scheduler.select_next() == "model_d"


class TestDifferentTimesteps:
    class AdapterA(_Base):
        input_types = []
        output_types = RecordA

    class AdapterB(_Base):
        input_types = Input(name="record_a", from_=RecordA, select=Fields("id"))
        output_types = RecordB

    class AdapterBLatest(_Base):
        input_types = Input(
            name="record_a",
            from_=RecordA,
            select=Fields("id"),
            read_policy=Latest(by="id"),
        )
        output_types = RecordB

    def test_window_policy(self):
        # B listed first, but Window forces A to run first every time (rank 0 vs 1)
        scheduler = Scheduler(
            [spec("b", self.AdapterB, dt=3.0), spec("a", self.AdapterA, dt=2.0)],
            duration=timedelta(seconds=6.0),
        )
        assert scheduler.select_next() == "a"  # B blocked, needs A at t≥2
        scheduler.sync_model_time("a", 2.0)
        assert scheduler.select_next() == "b"
        scheduler.sync_model_time("b", 3.0)
        assert scheduler.select_next() == "a"  # B blocked, needs A at t≥4
        scheduler.sync_model_time("a", 4.0)
        assert scheduler.select_next() == "b"

    def test_latest_policy(self):
        # B listed first and has no dependency — runs before A at t=0
        scheduler = Scheduler(
            [spec("b", self.AdapterBLatest, dt=3.0), spec("a", self.AdapterA, dt=2.0)],
            duration=timedelta(seconds=6.0),
        )
        assert scheduler.select_next() == "b"  # no dependency, listed first
        scheduler.sync_model_time("b", 3.0)
        assert scheduler.select_next() == "a"
        scheduler.sync_model_time("a", 2.0)
        assert scheduler.select_next() == "a"
        scheduler.sync_model_time("a", 4.0)
        assert scheduler.select_next() == "b"


class TestTimeUnits:
    def test_times_are_seconds(self):
        scheduler = Scheduler(
            [spec("model_a", AdapterA, dt=60.0)], duration=timedelta(hours=1)
        )
        assert scheduler.duration == 3600.0
        assert scheduler.timestep_lengths == {"model_a": 60.0}
        assert scheduler.time_window("model_a") == TimeWindow(0.0, 60.0)

    def test_bare_number_duration_rejected(self):
        with pytest.raises(TypeError, match="duration must be a datetime.timedelta"):
            Scheduler([spec("model_a", AdapterA)], duration=3600.0)


class TestCompleteStep:
    def _scheduler(self):
        return Scheduler(
            [spec("model_a", AdapterA, dt=60.0)], duration=timedelta(hours=1)
        )

    def test_accepts_window_end(self):
        scheduler = self._scheduler()
        scheduler.complete_step("model_a", 60.0)
        assert scheduler.model_times["model_a"] == 60.0

    def test_stores_exact_window_end_within_tolerance(self):
        scheduler = self._scheduler()
        scheduler.complete_step("model_a", 60.0 + 1e-9)
        assert scheduler.model_times["model_a"] == 60.0

    @pytest.mark.parametrize(
        "reported",
        [0.0, 30.0, 120.0, None, "60"],
        ids=["no-progress", "short-step", "long-step", "none", "string"],
    )
    def test_rejects_other_times(self, reported):
        scheduler = self._scheduler()
        with pytest.raises(RuntimeError, match="'model_a' reported time"):
            scheduler.complete_step("model_a", reported)
        assert scheduler.model_times["model_a"] == 0.0


class TestActiveWindow:
    def test_late_model_starts_at_its_active_from(self):
        scheduler = Scheduler(
            [spec("model_a", AdapterA, active_from=timedelta(seconds=2))],
            duration=timedelta(seconds=5),
        )
        assert scheduler.model_times["model_a"] == 2.0
        assert scheduler.time_window("model_a") == TimeWindow(2.0, 3.0)

    def test_model_stops_at_its_active_until(self):
        scheduler = Scheduler(
            [spec("model_a", AdapterA, active_until=timedelta(seconds=2))],
            duration=timedelta(seconds=5),
        )
        scheduler.sync_model_time("model_a", 2.0)
        assert scheduler.select_next() is None

    def test_model_takes_only_whole_steps(self):
        scheduler = Scheduler(
            [spec("model_a", AdapterA, dt=60.0, active_until=timedelta(seconds=150))],
            duration=timedelta(hours=1),
        )
        assert scheduler.final_times["model_a"] == 120.0

    def test_dependent_waits_on_steps_of_late_dependency(self):
        # model_a steps at 1, 3, 5, ...; model_b's window [2, 3) needs
        # model_a only through 1, its last step boundary before 3.
        scheduler = Scheduler(
            [
                spec("model_a", AdapterA, dt=2.0, active_from=timedelta(seconds=1)),
                spec("model_b", AdapterB, dependencies=("model_a",)),
            ],
            duration=timedelta(seconds=6),
        )
        scheduler.sync_model_time("model_b", 2.0)
        assert scheduler._eligible("model_b")
        scheduler.sync_model_time("model_b", 3.0)
        assert not scheduler._eligible("model_b")

    def test_dependent_runs_on_after_dependency_stops(self):
        scheduler = Scheduler(
            [
                spec("model_a", AdapterA, active_until=timedelta(seconds=2)),
                spec("model_b", AdapterB, dependencies=("model_a",)),
            ],
            duration=timedelta(seconds=4),
        )
        scheduler.sync_model_time("model_a", 2.0)
        scheduler.sync_model_time("model_b", 3.0)
        assert scheduler.select_next() == "model_b"

    def test_active_until_after_run_rejected(self):
        with pytest.raises(ValueError, match="after the end of the run"):
            Scheduler(
                [spec("model_a", AdapterA, active_until=timedelta(seconds=6))],
                duration=timedelta(seconds=5),
            )

    def test_active_from_at_run_end_rejected(self):
        with pytest.raises(ValueError, match="no whole timestep"):
            Scheduler(
                [spec("model_a", AdapterA, active_from=timedelta(seconds=5))],
                duration=timedelta(seconds=5),
            )

    def test_timestep_longer_than_active_time_rejected(self):
        with pytest.raises(ValueError, match="no whole timestep"):
            Scheduler(
                [spec("model_a", AdapterA, dt=60.0)], duration=timedelta(seconds=30)
            )
