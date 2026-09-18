import pytest
from datetime import timedelta
from adapter.adapter import Adapter
from base import ModelSpec
from supervisory.supervisory_model import SupervisoryModel
from supervisory.comm.messages import Registration
from _records import Vehicle, Battery
from unittest.mock import MagicMock
from supervisory.comm.messages import Response

model = SupervisoryModel(
    model_specs=[],
    state_memory=None,
    rabbitmq_client=None,
    max_global_time=timedelta(0),
    data_adapters=[],
)

vehicle_payload_1 = {"veh_id": "veh_1", "time": 1.0, "soc": 0.5}
vehicle_payload_2 = {"veh_id": "veh_2", "time": 2.0, "soc": 0.7}
battery_payload = {"veh_id": "veh_1", "capacity": 100}


class TestDeserialize:
    def test_single_record(self):
        outputs = model._deserialize(Vehicle, [vehicle_payload_1])
        assert outputs[0] == Vehicle(veh_id="veh_1", soc=0.5, time=1.0)

    def test_multiple_payloads(self):
        outputs = model._deserialize(Vehicle, [vehicle_payload_1, vehicle_payload_2])
        assert outputs == [
            Vehicle(veh_id="veh_1", soc=0.5, time=1.0),
            Vehicle(veh_id="veh_2", soc=0.7, time=2.0),
        ]

    def test_multiple_record_types(self):
        tagged_vehicle = {"_type": "Vehicle", **vehicle_payload_1}
        tagged_battery = {"_type": "Battery", **battery_payload}
        outputs = model._deserialize(
            [Vehicle, Battery], [tagged_vehicle, tagged_battery]
        )
        assert outputs == [
            Vehicle(veh_id="veh_1", soc=0.5, time=1.0),
            Battery(veh_id="veh_1", capacity=100),
        ]


def test_record_types_deduplicates():
    original_output_types = model.output_types
    original_constant_types = model.constant_types
    try:
        model.output_types = {
            "transportation": Vehicle,
            "charging": Battery,
        }
        model.constant_types = {
            "transportation": Vehicle,
            "charging": Battery,
        }
        assert model._record_types() == [Vehicle, Battery]
    finally:
        model.output_types = original_output_types
        model.constant_types = original_constant_types


def test_record_types_flattens_lists():
    original_output_types = model.output_types
    original_constant_types = model.constant_types
    try:
        model.output_types = {"transportation": [Vehicle, Battery]}
        model.constant_types = {}
        assert model._record_types() == [Vehicle, Battery]
    finally:
        model.output_types = original_output_types
        model.constant_types = original_constant_types


class TestValidateRegistration:
    def setup_method(self):
        model.scheduler.timestep_lengths = {"model_a": 1.0}

    def test_accepts_known_worker_with_correct_timestep(self):
        accepted, error = model._validate_registration(
            Registration(
                name="model_a", routing_key="rk", metadata={"timestep_length": 1.0}
            )
        )
        assert accepted is True
        assert error is None

    def test_rejects_unknown_worker(self):
        accepted, error = model._validate_registration(
            Registration(name="model_b", routing_key="rk", metadata={})
        )
        assert accepted is False
        assert "model_b" in error

    def test_rejects_timestep_mismatch(self):
        accepted, error = model._validate_registration(
            Registration(
                name="model_a", routing_key="rk", metadata={"timestep_length": 2.0}
            )
        )
        assert accepted is False
        assert "model_a" in error

    def test_rejects_missing_timestep_in_metadata(self):
        accepted, error = model._validate_registration(
            Registration(name="model_a", routing_key="rk", metadata=None)
        )
        assert accepted is False
        assert "model_a" in error


def test_wait_for_all_raises_on_error():
    failed_response = Response(success=False, error="error")
    with pytest.raises(RuntimeError, match=f"failed for"):
        model._wait_for_all(
            {"model_a": failed_response}, ["model_a"], "advance", timeout=0.0
        )


def test_wait_for_all_succesful_response():
    successful_response = Response(success=True)
    model._wait_for_all(
        {"model_a": successful_response}, ["model_a"], "advance", timeout=0.0
    )


def test_terminate_stores_final_outputs_before_closing():
    calls = []
    state_memory = MagicMock()
    state_memory.insert_outputs.side_effect = lambda cls, rows: calls.append(
        ("insert", cls, rows)
    )
    state_memory.close_conn.side_effect = lambda: calls.append(("close",))
    supervisor = SupervisoryModel(
        model_specs=[],
        state_memory=state_memory,
        rabbitmq_client=MagicMock(),
        max_global_time=timedelta(0),
    )
    supervisor.display = MagicMock()
    supervisor.output_types = {"model_a": Vehicle, "model_b": Vehicle}
    supervisor._broadcast = MagicMock(
        return_value={
            "model_a": Response(success=True, payload=[vehicle_payload_1]),
            "model_b": Response(success=True),
        }
    )

    supervisor.terminate()

    assert calls == [("insert", Vehicle, [vehicle_payload_1]), ("close",)]


def test_wait_for_all_raises_on_timeout():
    mock_client = MagicMock()
    model.rabbitmq_client = mock_client
    try:
        with pytest.raises(TimeoutError, match="model_a"):
            model._wait_for_all({}, ["model_a"], "advance", timeout=0.0)
    finally:
        model.rabbitmq_client = None


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
            ModelSpec(name="model_a", adapter=StepAdapter, timestep_length=timedelta(seconds=10))
        ],
        state_memory=None,
        rabbitmq_client=MagicMock(),
        max_global_time=timedelta(minutes=1),
    )
    supervisor._send_and_wait = MagicMock(return_value=Response(success=True, payload=10.0))
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
            max_global_time=timedelta(0),
            shutdown_timeout=0.0,
        )
        supervisor.display = MagicMock()
        supervisor.routing_keys = {"model_a": "model_a", "model_b": "model_b"}
        # Workers confirm termination right away.
        client.terminate.side_effect = lambda key, on_ack: on_ack(
            Response(success=True)
        )
        return supervisor

    def test_failed_run_stops_workers_and_closes(self):
        supervisor = self._supervisor()
        supervisor._run_steps = MagicMock(side_effect=TimeoutError("advance timed out"))
        supervisor.terminate = MagicMock()

        with pytest.raises(TimeoutError, match="advance timed out"):
            supervisor.run()

        terminated = [c.args[0] for c in supervisor.rabbitmq_client.terminate.call_args_list]
        assert terminated == ["model_a", "model_b"]
        supervisor.state_memory.close_conn.assert_called_once()
        supervisor.terminate.assert_not_called()

    def test_ctrl_c_also_stops_workers(self):
        supervisor = self._supervisor()
        supervisor._run_steps = MagicMock(side_effect=KeyboardInterrupt)
        with pytest.raises(KeyboardInterrupt):
            supervisor.run()
        assert supervisor.rabbitmq_client.terminate.call_count == 2
        supervisor.state_memory.close_conn.assert_called_once()

    def test_cleanup_problems_do_not_hide_original_error(self):
        state_memory = MagicMock()
        state_memory.close_conn.side_effect = RuntimeError("pool broken")
        supervisor = self._supervisor(state_memory)
        supervisor.rabbitmq_client.terminate.side_effect = ConnectionError("broker gone")
        supervisor._run_steps = MagicMock(side_effect=ValueError("bad input"))

        with pytest.raises(ValueError, match="bad input"):
            supervisor.run()
        warnings = [c.args[0] for c in supervisor.display.warning.call_args_list]
        assert any("broker gone" in w for w in warnings)
        assert any("pool broken" in w for w in warnings)

    def test_unconfirmed_termination_is_reported(self):
        supervisor = self._supervisor()
        supervisor.rabbitmq_client.terminate.side_effect = None  # never confirms
        supervisor._run_steps = MagicMock(side_effect=ValueError("bad input"))
        with pytest.raises(ValueError):
            supervisor.run()
        warnings = [c.args[0] for c in supervisor.display.warning.call_args_list]
        assert any("['model_a', 'model_b']" in w for w in warnings)

    def test_failed_terminate_still_closes(self):
        supervisor = self._supervisor()
        supervisor._broadcast = MagicMock(side_effect=RuntimeError("terminate failed"))
        with pytest.raises(RuntimeError, match="terminate failed"):
            supervisor.terminate()
        supervisor.state_memory.close_conn.assert_called_once()
        supervisor.display.end_run.assert_not_called()
