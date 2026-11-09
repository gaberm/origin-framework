import pytest
from datetime import datetime, timedelta
from adapter.adapter import Adapter
from dataclasses import dataclass
from base import Entity, StartObservation, ModelSpec
from supervisory.supervisory_model import SupervisoryModel
from supervisory.comm.messages import Operation, Registration
from _records import Vehicle, Battery
from unittest.mock import MagicMock
from supervisory.comm.messages import Response

model = SupervisoryModel(
    model_specs=[],
    state_memory=None,
    rabbitmq_client=None,
    duration=timedelta(seconds=1),
)

vehicle_dict_1 = {"veh_id": "veh_1", "time_s": 1.0, "soc": 0.5}
vehicle_dict_2 = {"veh_id": "veh_2", "time_s": 2.0, "soc": 0.7}
battery_dict = {"veh_id": "veh_1", "capacity": 100}


class TestDeserialize:
    def test_single_record(self):
        outputs = model._unpack_records(Vehicle, [vehicle_dict_1])
        assert outputs[0] == Vehicle(veh_id="veh_1", soc=0.5, time_s=1.0)

    def test_multiple_records(self):
        outputs = model._unpack_records(Vehicle, [vehicle_dict_1, vehicle_dict_2])
        assert outputs == [
            Vehicle(veh_id="veh_1", soc=0.5, time_s=1.0),
            Vehicle(veh_id="veh_2", soc=0.7, time_s=2.0),
        ]

    def test_multiple_record_types(self):
        tagged_vehicle = {"_type": "Vehicle", **vehicle_dict_1}
        tagged_battery = {"_type": "Battery", **battery_dict}
        outputs = model._unpack_records(
            [Vehicle, Battery], [tagged_vehicle, tagged_battery]
        )
        assert outputs == [
            Vehicle(veh_id="veh_1", soc=0.5, time_s=1.0),
            Battery(veh_id="veh_1", capacity=100),
        ]


def test_record_types_deduplicates():
    original_output_types = model.output_types
    try:
        model.output_types = {
            "transportation": [Vehicle, Battery],
            "charging": Battery,
        }
        assert model._record_types() == [Vehicle, Battery]
    finally:
        model.output_types = original_output_types


def test_record_types_flattens_lists():
    original_output_types = model.output_types
    try:
        model.output_types = {"transportation": [Vehicle, Battery]}
        assert model._record_types() == [Vehicle, Battery]
    finally:
        model.output_types = original_output_types


def test_record_types_add_start_observations():
    class Departure(StartObservation):
        charge_wh: float

    @dataclass(kw_only=True)
    class Trip(Entity):
        id_field = "veh_id"
        start_observation = Departure

        veh_id: str

    original_output_types = model.output_types
    try:
        model.output_types = {"transportation": [Trip, Vehicle]}
        assert model._record_types() == [Trip, Departure, Vehicle]
    finally:
        model.output_types = original_output_types


class TestValidateRegistration:
    timing = {"timestep_length": 1.0, "active_from": 0.0, "active_until": None}

    def setup_method(self):
        model.timings = {"model_a": self.timing}

    def test_accepts_known_worker_with_matching_timing(self):
        accepted, error = model._validate_registration(
            Registration(name="model_a", routing_key="rk", metadata=self.timing)
        )
        assert accepted is True
        assert error is None

    def test_rejects_unknown_worker(self):
        accepted, error = model._validate_registration(
            Registration(name="model_b", routing_key="rk", metadata={})
        )
        assert accepted is False
        assert "model_b" in error

    @pytest.mark.parametrize(
        "changed_timing",
        [{"timestep_length": 2.0}, {"active_from": 60.0}, {"active_until": 3600.0}],
        ids=["timestep", "active_from", "active_until"],
    )
    def test_rejects_timing_mismatch(self, changed_timing):
        accepted, error = model._validate_registration(
            Registration(
                name="model_a",
                routing_key="rk",
                metadata={**self.timing, **changed_timing},
            )
        )
        assert accepted is False
        assert "timing mismatch for 'model_a'" in error

    def test_rejects_missing_timing_in_metadata(self):
        accepted, error = model._validate_registration(
            Registration(name="model_a", routing_key="rk", metadata=None)
        )
        assert accepted is False
        assert "model_a" in error


class TestStartTime:
    def test_sent_to_workers_when_they_register(self):
        client = MagicMock()
        supervisor = SupervisoryModel(
            model_specs=[],
            state_memory=None,
            rabbitmq_client=client,
            duration=timedelta(hours=1),
            start_time=datetime(2026, 1, 18, 18),
        )
        supervisor.display = MagicMock()
        supervisor.await_workers()
        payload = client.collect_registrations.call_args.kwargs["accepted_payload"]
        assert payload == {"start_time": "2026-01-18T18:00:00"}

    def test_bare_string_rejected(self):
        with pytest.raises(TypeError, match="start_time must be a datetime.datetime"):
            SupervisoryModel(
                model_specs=[],
                state_memory=None,
                rabbitmq_client=None,
                duration=timedelta(hours=1),
                start_time="2026-01-18",
            )


def test_terminate_stores_final_outputs_before_closing():
    calls = []
    state_memory = MagicMock()
    state_memory.insert_records.side_effect = lambda records: calls.append(
        ("insert", records)
    )
    state_memory.close_conn.side_effect = lambda: calls.append(("close",))
    supervisor = SupervisoryModel(
        model_specs=[],
        state_memory=state_memory,
        rabbitmq_client=MagicMock(),
        duration=timedelta(seconds=1),
    )
    supervisor.display = MagicMock()
    supervisor.output_types = {"model_a": Vehicle, "model_b": Vehicle}
    supervisor.scheduler.model_times = {"model_a": 1.0, "model_b": 1.0}
    supervisor.scheduler.timestep_lengths = {"model_a": 1.0, "model_b": 1.0}
    supervisor.rabbitmq_client.call_all.return_value = {
        "model_a": [vehicle_dict_1],
        "model_b": None,
    }

    supervisor.terminate()

    assert calls == [("insert", [Vehicle(**vehicle_dict_1)]), ("close",)]


class TestRecordTimes:
    @dataclass(kw_only=True)
    class Car(Entity):
        id_field = "veh_id"
        veh_id: str

    def _supervisor(self):
        state_memory = MagicMock()
        supervisor = SupervisoryModel(
            model_specs=[],
            state_memory=state_memory,
            rabbitmq_client=MagicMock(),
            duration=timedelta(seconds=1),
        )
        supervisor.output_types = {"model_a": [Vehicle, self.Car]}
        # model_a just stepped from 10 s to 20 s.
        supervisor.scheduler.model_times = {"model_a": 20.0}
        supervisor.scheduler.timestep_lengths = {"model_a": 10.0}
        return supervisor

    @pytest.mark.parametrize(
        "time_s", [10.0, 15.0, 20.0, 25.0], ids=["start", "middle", "end", "future"]
    )
    def test_stores_records_from_step_on(self, time_s):
        supervisor = self._supervisor()
        supervisor.read_outputs(
            "model_a", [{"_type": "Vehicle", **vehicle_dict_1, "time_s": time_s}]
        )
        supervisor.state_memory.insert_records.assert_called_once()

    def test_rejects_timestamp_before_step(self):
        supervisor = self._supervisor()
        with pytest.raises(
            RuntimeError,
            match=r"time_s=5.0 s, before the step it just took \[10.0, 20.0\] s",
        ):
            supervisor.read_outputs(
                "model_a", [{"_type": "Vehicle", **vehicle_dict_1, "time_s": 5.0}]
            )
        supervisor.state_memory.insert_records.assert_not_called()

    def test_stores_entity_that_started_before_step(self):
        supervisor = self._supervisor()
        supervisor.read_outputs(
            "model_a",
            [{"_type": "Car", "veh_id": "veh_1", "start_s": 0.0, "end_s": 20.0}],
        )
        supervisor.state_memory.insert_records.assert_called_once()

    @pytest.mark.parametrize("time_field", ["start_s", "end_s"])
    def test_rejects_entity_time_after_step(self, time_field):
        supervisor = self._supervisor()
        car = {"_type": "Car", "veh_id": "veh_1", "start_s": 0.0, "end_s": None}
        car[time_field] = 25.0
        with pytest.raises(
            RuntimeError, match=f"{time_field}=25.0 s, after the end of the step"
        ):
            supervisor.read_outputs("model_a", [car])


def test_initialize_components_stores_returned_outputs():
    state_memory = MagicMock()
    client = MagicMock()
    supervisor = SupervisoryModel(
        model_specs=[],
        state_memory=state_memory,
        rabbitmq_client=client,
        duration=timedelta(seconds=1),
    )
    supervisor.display = MagicMock()
    supervisor.model_names = ["charging", "sumo"]
    supervisor.routing_keys = {"charging": "charging", "sumo": "sumo"}
    supervisor.output_types = {"charging": Battery, "sumo": Vehicle}
    client.call_all.return_value = {"charging": [battery_dict], "sumo": None}

    supervisor.initialize_components()

    state_memory.insert_records.assert_called_once_with([Battery(**battery_dict)])


def test_advance_checks_reported_time():
    class StepAdapter(Adapter):
        input_types = []
        output_types = Vehicle

        def initialize(self): ...
        def current_time(self): ...
        def read_outputs(self): ...
        def write_inputs(self, inputs): ...
        def advance(self): ...
        def terminate(self): ...

    supervisor = SupervisoryModel(
        model_specs=[
            ModelSpec(
                name="model_a",
                adapter=StepAdapter,
                timestep_length=timedelta(seconds=10),
            )
        ],
        state_memory=None,
        rabbitmq_client=MagicMock(),
        duration=timedelta(minutes=1),
    )
    supervisor.rabbitmq_client.call.return_value = 10.0
    supervisor.advance("model_a")
    assert supervisor.scheduler.model_times["model_a"] == 10.0

    # A model that doesn't progress stops the run instead of looping.
    with pytest.raises(RuntimeError, match="reported time 10.0 s"):
        supervisor.advance("model_a")


class TestShutdownAfterError:
    def _supervisor(self, state_memory=None):
        client = MagicMock()
        supervisor = SupervisoryModel(
            model_specs=[],
            state_memory=state_memory or MagicMock(),
            rabbitmq_client=client,
            duration=timedelta(seconds=1),
            shutdown_timeout=0.0,
        )
        supervisor.display = MagicMock()
        supervisor.routing_keys = {"model_a": "model_a", "model_b": "model_b"}
        # Workers confirm termination right away.
        client.call_all.return_value = {"model_a": None, "model_b": None}
        return supervisor

    def test_failed_run_stops_workers_and_closes(self):
        supervisor = self._supervisor()
        supervisor._run_steps = MagicMock(side_effect=TimeoutError("advance timed out"))
        supervisor.terminate = MagicMock()

        with pytest.raises(TimeoutError, match="advance timed out"):
            supervisor.run()

        supervisor.rabbitmq_client.call_all.assert_called_once_with(
            {"model_a": "model_a", "model_b": "model_b"},
            Operation.TERMINATE,
            timeout=0.0,
        )
        supervisor.state_memory.close_conn.assert_called_once()
        supervisor.terminate.assert_not_called()

    def test_ctrl_c_also_stops_workers(self):
        supervisor = self._supervisor()
        supervisor._run_steps = MagicMock(side_effect=KeyboardInterrupt)
        with pytest.raises(KeyboardInterrupt):
            supervisor.run()
        supervisor.rabbitmq_client.call_all.assert_called_once()
        supervisor.state_memory.close_conn.assert_called_once()

    def test_cleanup_problems_do_not_hide_original_error(self):
        state_memory = MagicMock()
        state_memory.close_conn.side_effect = RuntimeError("pool broken")
        supervisor = self._supervisor(state_memory)
        supervisor.rabbitmq_client.call_all.side_effect = ConnectionError("broker gone")
        supervisor._run_steps = MagicMock(side_effect=ValueError("bad input"))

        with pytest.raises(ValueError, match="bad input"):
            supervisor.run()
        warnings = [c.args[0] for c in supervisor.display.warning.call_args_list]
        assert any("broker gone" in w for w in warnings)
        assert any("pool broken" in w for w in warnings)

    def test_unconfirmed_termination_is_reported(self):
        supervisor = self._supervisor()
        supervisor.rabbitmq_client.call_all.side_effect = TimeoutError(
            "terminate timed out; no response from: ['model_a', 'model_b']"
        )
        supervisor._run_steps = MagicMock(side_effect=ValueError("bad input"))
        with pytest.raises(ValueError):
            supervisor.run()
        warnings = [c.args[0] for c in supervisor.display.warning.call_args_list]
        assert any("['model_a', 'model_b']" in w for w in warnings)

    def test_failed_terminate_still_closes(self):
        supervisor = self._supervisor()
        supervisor.rabbitmq_client.call_all.side_effect = RuntimeError(
            "terminate failed"
        )
        with pytest.raises(RuntimeError, match="terminate failed"):
            supervisor.terminate()
        supervisor.state_memory.close_conn.assert_called_once()
        supervisor.display.end_run.assert_not_called()
