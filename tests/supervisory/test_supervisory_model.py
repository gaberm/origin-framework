import pytest
from supervisory.supervisory_model import SupervisoryModel
from supervisory.comm.messages import Registration
from _records import Vehicle, Battery
from unittest.mock import MagicMock
from supervisory.comm.messages import Response

model = SupervisoryModel(
    model_specs=[],
    state_memory=None,
    rabbitmq_client=None,
    max_global_time=0.0,
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


def test_wait_for_all_raises_on_timeout():
    mock_client = MagicMock()
    model.rabbitmq_client = mock_client
    try:
        with pytest.raises(TimeoutError, match="model_a"):
            model._wait_for_all({}, ["model_a"], "advance", timeout=0.0)
    finally:
        model.rabbitmq_client = None
