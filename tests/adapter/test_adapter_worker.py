import json
from datetime import datetime, timedelta
import numpy as np
import pytest
from unittest.mock import MagicMock, patch
from adapter.adapter_worker import AdapterWorker
from supervisory.comm.messages import Message, Response, Operation
from dataclasses import dataclass
from base import Entity
from _records import Vehicle, Battery


@dataclass(kw_only=True)
class Depot(Entity):
    id_field = "depot_id"
    initialize_only = True

    depot_id: str


def _spec(**params):
    spec = MagicMock()
    spec.name = "test_worker"
    spec.routing_key = "test_rk"
    spec.queue_name = "test_queue"
    spec.timestep_length = timedelta(seconds=1)
    spec.active_from = timedelta(hours=2)
    spec.timing = {"timestep_length": 1.0, "active_from": 7200.0, "active_until": None}
    spec.params = params
    spec.adapter.return_value.timestep_length = spec.timestep_length
    # After initialize() the model is where its first step starts.
    spec.adapter.return_value.current_time.return_value = spec.active_from
    spec.adapter.return_value.start_time = None
    return spec


@pytest.fixture
def worker():
    with patch("adapter.adapter_worker.pika.BlockingConnection"):
        return AdapterWorker(
            host="localhost",
            port=5672,
            username="guest",
            password="guest",
            spec=_spec(),
            run_id="run_test",
        )


class TestRunScopedMessaging:
    def test_builds_adapter_from_spec_and_run(self):
        spec = _spec(sumo_config="main.sumocfg")
        with patch("adapter.adapter_worker.pika.BlockingConnection"):
            worker = AdapterWorker(
                "localhost", 5672, "guest", "guest", spec=spec, run_id="run_test"
            )
        spec.adapter.assert_called_once_with(
            name="test_worker",
            run_id="run_test",
            timestep_length=timedelta(seconds=1),
            sumo_config="main.sumocfg",
        )
        assert worker.adapter is spec.adapter.return_value
        assert worker.adapter.active_from == timedelta(hours=2)

    def test_queue_and_exchange_are_scoped_to_run(self, worker):
        worker.channel.queue_declare.assert_called_once_with(
            queue="run_test.test_queue", durable=True, auto_delete=True
        )
        worker.channel.queue_bind.assert_called_once_with(
            queue="run_test.test_queue",
            exchange="run_test.tasks",
            routing_key="test_rk",
        )
        worker.channel.exchange_declare.assert_called_once_with(
            exchange="run_test.tasks", exchange_type="direct", auto_delete=True
        )

    def test_consumes_commands_only_after_registering(self, worker):
        calls = []
        worker.register = lambda: calls.append("register")
        worker.channel.basic_consume.side_effect = (
            lambda **kwargs: calls.append(("consume", kwargs["queue"]))
        )
        worker.channel.start_consuming.side_effect = lambda: calls.append("start")

        # No command consumer before run().
        worker.channel.basic_consume.assert_not_called()
        worker.run()

        assert calls == ["register", ("consume", "run_test.test_queue"), "start"]

    def test_registers_on_run_scoped_queue(self, worker):
        worker.channel.basic_consume.return_value = "tag"
        worker.registration_timeout = 0.0
        with pytest.raises(TimeoutError):
            worker.register()
        worker.channel.queue_declare.assert_any_call(
            queue="run_test.worker_registration", durable=True, auto_delete=True
        )
        publish = worker.channel.basic_publish.call_args.kwargs
        assert publish["routing_key"] == "run_test.worker_registration"
        # The timing is sent in seconds.
        assert json.loads(publish["body"])["metadata"] == {
            "timestep_length": 1.0,
            "active_from": 7200.0,
            "active_until": None,
        }


class TestReadOutputs:
    def test_single_type(self, worker):
        worker.adapter.output_types = Vehicle
        worker.adapter.read_outputs.return_value = [
            Vehicle(veh_id="veh_1", soc=0.5, time_s=1.0)
        ]

        response = worker.read_outputs(None)

        assert response == Response(
            success=True, payload=[{"veh_id": "veh_1", "soc": 0.5, "time_s": 1.0}]
        )

    def test_multi_type_adds_type_tag(self, worker):
        worker.adapter.output_types = [Vehicle, Battery]
        worker.adapter.read_outputs.return_value = [
            Vehicle(veh_id="veh_1", soc=0.5, time_s=1.0),
            Battery(veh_id="veh_1", capacity=100),
        ]

        response = worker.read_outputs(None)

        assert response == Response(
            success=True,
            payload=[
                {"_type": "Vehicle", "veh_id": "veh_1", "soc": 0.5, "time_s": 1.0},
                {"_type": "Battery", "veh_id": "veh_1", "capacity": 100},
            ],
        )


class TestInitialize:
    def test_without_outputs(self, worker):
        worker.adapter.initialize.return_value = None

        assert worker.initialize(None) == Response(success=True)

    def test_returns_initialize_only_outputs(self, worker):
        worker.adapter.output_types = [Vehicle, Depot]
        worker.adapter.initialize.return_value = [
            Depot(depot_id="d1", start_s=None)
        ]

        response = worker.initialize(None)

        assert response == Response(
            success=True,
            payload=[
                {"_type": "Depot", "depot_id": "d1", "start_s": None, "end_s": None}
            ],
        )


    def test_rejects_unflagged_records(self, worker):
        worker.adapter.output_types = [Vehicle, Depot]
        worker.adapter.initialize.return_value = [
            Depot(depot_id="d1", start_s=None),
            Vehicle(veh_id="veh_1", soc=0.5, time_s=0.0),
        ]

        with pytest.raises(
            ValueError,
            match=r"initialize\(\) returned \['Vehicle'\], which aren't initialize_only",
        ):
            worker.initialize(None)


class TestStartsAtActiveFrom:
    def test_names_run_times_when_run_has_no_start_time(self, worker):
        worker.adapter.current_time.return_value = timedelta(0)

        with pytest.raises(
            ValueError,
            match=r"is at 0:00:00 after initialize\(\), but its first step "
            r"starts at 2:00:00 \(ModelSpec.active_from\)$",
        ):
            worker.initialize(None)

    def test_names_calendar_times_when_run_has_start_time(self, worker):
        worker.adapter.current_time.return_value = timedelta(hours=20)
        worker.adapter.start_time = datetime(2026, 1, 18)

        with pytest.raises(
            ValueError,
            match=r"is at 2026-01-18 20:00:00 after initialize\(\), but its "
            r"first step starts at 2026-01-18 02:00:00 \(ModelSpec.active_from\)$",
        ):
            worker.initialize(None)


class TestTerminate:
    def test_without_final_outputs(self, worker):
        worker.adapter.terminate.return_value = None

        assert worker.terminate(None) == Response(success=True)

    def test_returns_final_outputs(self, worker):
        worker.adapter.output_types = Vehicle
        worker.adapter.terminate.return_value = [
            Vehicle(veh_id="veh_1", soc=0.5, time_s=1.0)
        ]

        response = worker.terminate(None)

        assert response == Response(
            success=True, payload=[{"veh_id": "veh_1", "soc": 0.5, "time_s": 1.0}]
        )


class TestOnMessage:
    def _call_raw(self, worker, body):
        mock_ch = MagicMock()
        worker._on_message(mock_ch, MagicMock(), MagicMock(), body)
        mock_ch.basic_ack.assert_called_once()
        return Response.from_dict(
            json.loads(mock_ch.basic_publish.call_args.kwargs["body"])
        )

    def _call(self, worker, operation):
        return self._call_raw(worker, json.dumps(Message(operation).to_dict()))

    def test_dispatches_known_command(self, worker):
        worker.adapter.initialize.return_value = None
        reply = self._call(worker, Operation.INITIALIZE)
        assert reply == Response(success=True)

    def test_wraps_adapter_exception(self, worker):
        worker.adapter.initialize.side_effect = RuntimeError("adapter failed")
        reply = self._call(worker, Operation.INITIALIZE)
        assert reply == Response(success=False, error="RuntimeError: adapter failed")

    def test_converts_numpy_values(self, worker):
        worker.adapter.output_types = Vehicle
        worker.adapter.read_outputs.return_value = [
            Vehicle(veh_id="veh_1", soc=np.float32(0.5), time_s=1.0)
        ]
        reply = self._call(worker, Operation.READ_OUTPUTS)
        assert reply == Response(
            success=True, payload=[{"veh_id": "veh_1", "soc": 0.5, "time_s": 1.0}]
        )
        assert type(reply.payload[0]["soc"]) is float

    def test_converts_numpy_arrays(self, worker):
        worker.adapter.output_types = Vehicle
        worker.adapter.read_outputs.return_value = [
            Vehicle(veh_id="veh_1", soc=np.array([1.5, 2.5], dtype=np.float32), time_s=1.0)
        ]
        reply = self._call(worker, Operation.READ_OUTPUTS)
        assert reply.payload[0]["soc"] == [1.5, 2.5]

    def test_unserializable_reply_becomes_error(self, worker):
        worker.adapter.output_types = Vehicle
        worker.adapter.read_outputs.return_value = [
            Vehicle(veh_id="veh_1", soc=object(), time_s=1.0)
        ]
        reply = self._call(worker, Operation.READ_OUTPUTS)
        assert not reply.success
        assert reply.error.startswith("TypeError: Object of type object")

    def test_malformed_body_becomes_error(self, worker):
        reply = self._call_raw(worker, "not json")
        assert not reply.success
        assert reply.error.startswith("JSONDecodeError")

    def test_unknown_command_becomes_error(self, worker):
        reply = self._call_raw(worker, json.dumps({"command": "bogus"}))
        assert not reply.success
        assert reply.error.startswith("ValueError: 'bogus'")

    def test_advance_replies_with_model_time_in_seconds(self, worker):
        worker.adapter.current_time.return_value = timedelta(minutes=2)
        reply = self._call(worker, Operation.ADVANCE)
        worker.adapter.advance.assert_called_once()
        assert reply == Response(success=True, payload=120.0)

    def test_advance_rejects_non_timedelta_time(self, worker):
        worker.adapter.current_time.return_value = 120.0
        reply = self._call(worker, Operation.ADVANCE)
        assert not reply.success
        assert "current_time() must be a datetime.timedelta" in reply.error

    def test_stops_after_successful_terminate(self, worker):
        worker.adapter.terminate.return_value = None
        mock_ch = MagicMock()
        body = json.dumps(Message(Operation.TERMINATE).to_dict())
        worker._on_message(mock_ch, MagicMock(), MagicMock(), body)
        mock_ch.stop_consuming.assert_called_once()

    def test_keeps_consuming_after_failed_terminate(self, worker):
        worker.adapter.terminate.side_effect = RuntimeError("close failed")
        mock_ch = MagicMock()
        body = json.dumps(Message(Operation.TERMINATE).to_dict())
        worker._on_message(mock_ch, MagicMock(), MagicMock(), body)
        mock_ch.stop_consuming.assert_not_called()


class TestRegister:
    def _setup_reply(self, worker, response, fixed_corr):
        body = json.dumps(response.to_dict())

        def fake_consume(*args, **_):
            on_reply = args[1]
            on_reply(
                MagicMock(), MagicMock(), MagicMock(correlation_id=fixed_corr), body
            )
            return "tag"

        worker.channel.basic_consume.side_effect = fake_consume

    def test_accepted(self, worker):
        worker.adapter.start_time = None
        self._setup_reply(
            worker, Response(success=True, payload={"start_time": None}), "fixed-corr"
        )
        with patch("adapter.adapter_worker.uuid.uuid4", return_value="fixed-corr"):
            worker.register()  # should not raise
        assert worker.adapter.start_time is None

    def test_accepted_sets_run_start_time(self, worker):
        self._setup_reply(
            worker,
            Response(success=True, payload={"start_time": "2026-01-18T18:00:00+00:00"}),
            "fixed-corr",
        )
        with patch("adapter.adapter_worker.uuid.uuid4", return_value="fixed-corr"):
            worker.register()
        assert worker.adapter.start_time == datetime.fromisoformat(
            "2026-01-18T18:00:00+00:00"
        )

    def test_rejected_raises(self, worker):
        self._setup_reply(
            worker, Response(success=False, error="timing mismatch"), "fixed-corr"
        )
        with patch("adapter.adapter_worker.uuid.uuid4", return_value="fixed-corr"):
            with pytest.raises(RuntimeError, match="timing mismatch"):
                worker.register()

    def test_timeout_raises(self, worker):
        worker.channel.basic_consume.return_value = "tag"
        worker.registration_timeout = 0.0
        with pytest.raises(TimeoutError):
            worker.register()


def test_worker_sets_heartbeat_explicitly():
    from supervisory.comm.messages import DEFAULT_HEARTBEAT_SECONDS

    with patch("adapter.adapter_worker.pika.BlockingConnection"), patch(
        "adapter.adapter_worker.pika.ConnectionParameters"
    ) as parameters:
        AdapterWorker("localhost", 5672, "guest", "guest", _spec(), "run_test")
    assert parameters.call_args.kwargs["heartbeat"] == DEFAULT_HEARTBEAT_SECONDS
