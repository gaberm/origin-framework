import pytest
from dataclasses import dataclass
from base import Record, Input, Fields, ModelSpec, Join
from base.input.input import Latest
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


def spec(name, adapter, dt=1.0, dependencies=None):
    return ModelSpec(
        name=name, adapter=adapter, timestep_length=dt, dependencies=dependencies
    )


class TestSingleModel:
    class AdapterA(_Base):
        input_types = []
        output_types = RecordA

    def test_select_returns_model(self):
        scheduler = Scheduler([spec("model_a", self.AdapterA)], max_global_time=2.0)
        assert scheduler.select_next() == "model_a"

    def test_select_returns_none_when_done(self):
        scheduler = Scheduler([spec("model_a", self.AdapterA)], max_global_time=1.0)
        scheduler.sync_model_time("model_a", 1.0)
        assert scheduler.select_next() is None

    def test_time_window_is_correct(self):
        scheduler = Scheduler([spec("model_a", self.AdapterA)], max_global_time=1.0)
        assert scheduler.time_window("model_a") == TimeWindow(0.0, 1.0)

    def test_sync_time_updates_model_time(self):
        scheduler = Scheduler([spec("model_a", self.AdapterA)], max_global_time=2.0)
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
            max_global_time=2.0,
        )
        assert scheduler.select_next() == "model_a"
        scheduler.sync_model_time("model_a", 1.0)
        assert scheduler.select_next() == "model_b"

    def test_latest_does_not_block(self):
        scheduler = Scheduler(
            [spec("model_a", self.AdapterA), spec("model_b", self.AdapterBLatest)],
            max_global_time=2.0,
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
                max_global_time=2.0,
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
            max_global_time=2.0,
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
            on=Join((RecordB, "id"), (RecordC, "id")),
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
            max_global_time=2.0,
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
            max_global_time=6.0,
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
            max_global_time=6.0,
        )
        assert scheduler.select_next() == "b"  # no dependency, listed first
        scheduler.sync_model_time("b", 3.0)
        assert scheduler.select_next() == "a"
        scheduler.sync_model_time("a", 2.0)
        assert scheduler.select_next() == "a"
        scheduler.sync_model_time("a", 4.0)
        assert scheduler.select_next() == "b"
