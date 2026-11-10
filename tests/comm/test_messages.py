import json
import pytest
from _records import Battery, Vehicle
from comm.messages import payloads_to_records, records_to_payloads, task_queue


class TestRecordsToPayloads:
    def test_tags_each_record_with_its_type(self):
        payloads = records_to_payloads(
            [
                Vehicle(veh_id="veh_1", soc=0.5, time_s=1.0),
                Battery(veh_id="veh_1", capacity=100),
            ]
        )
        assert payloads == [
            {"_type": "Vehicle", "veh_id": "veh_1", "soc": 0.5, "time_s": 1.0},
            {"_type": "Battery", "veh_id": "veh_1", "capacity": 100},
        ]

    def test_empty(self):
        assert records_to_payloads([]) == []


class TestPayloadsToRecords:
    def test_single_declared_type(self):
        payloads = [
            {"_type": "Vehicle", "veh_id": "veh_1", "soc": 0.5, "time_s": 1.0},
            {"_type": "Vehicle", "veh_id": "veh_2", "soc": 0.7, "time_s": 2.0},
        ]
        assert payloads_to_records(payloads, Vehicle) == [
            Vehicle(veh_id="veh_1", soc=0.5, time_s=1.0),
            Vehicle(veh_id="veh_2", soc=0.7, time_s=2.0),
        ]

    def test_several_declared_types(self):
        payloads = [
            {"_type": "Vehicle", "veh_id": "veh_1", "soc": 0.5, "time_s": 1.0},
            {"_type": "Battery", "veh_id": "veh_1", "capacity": 100},
        ]
        assert payloads_to_records(payloads, [Vehicle, Battery]) == [
            Vehicle(veh_id="veh_1", soc=0.5, time_s=1.0),
            Battery(veh_id="veh_1", capacity=100),
        ]

    def test_undeclared_type_rejected(self):
        payloads = [{"_type": "Battery", "veh_id": "veh_1", "capacity": 100}]
        with pytest.raises(
            ValueError,
            match=r"received a Battery record, but the model declares \['Vehicle'\]",
        ):
            payloads_to_records(payloads, Vehicle)

    def test_leaves_the_given_dicts_unchanged(self):
        record_dict = {"_type": "Battery", "veh_id": "veh_1", "capacity": 100}
        payloads_to_records([record_dict], Battery)
        assert record_dict["_type"] == "Battery"


def test_round_trip_through_json():
    records = [
        Vehicle(veh_id="veh_1", soc=0.5, time_s=1.0),
        Battery(veh_id="veh_1", capacity=100),
    ]
    sent = json.loads(json.dumps(records_to_payloads(records)))
    assert payloads_to_records(sent, [Vehicle, Battery]) == records


def test_task_queue_is_scoped_to_run_and_model():
    assert task_queue("run_test", "sumo") == "run_test.sumo_queue"
