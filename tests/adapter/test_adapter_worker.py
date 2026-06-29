import json
import pytest
from unittest.mock import MagicMock, patch
from adapter.adapter_worker import AdapterWorker
from supervisory.comm.messages import Message, Response, Operation
from ..state_memory._records import Vehicle, Battery


@pytest.fixture
def worker():
    with patch("adapter.adapter_worker.pika.BlockingConnection"):
        mock_adapter = MagicMock()
        mock_adapter.name = "test_worker"
        mock_adapter.timestep_length = 1.0
        return AdapterWorker(
            host="localhost",
            port=5672,
            username="guest",
            password="guest",
            routing_key="test_rk",
            queue_name="test_queue",
            adapter=mock_adapter,
        )


class TestReadOutputs:
    def test_single_type(self, worker):
        worker.adapter.output_types = Vehicle
        worker.adapter.read_outputs.return_value = [
            Vehicle(veh_id="veh_1", soc=0.5, time=1.0)
        ]

        response = worker.read_outputs(None)

        assert response == Response(
            success=True, payload=[{"veh_id": "veh_1", "soc": 0.5, "time": 1.0}]
        )

    def test_multi_type_adds_type_tag(self, worker):
        worker.adapter.output_types = [Vehicle, Battery]
        worker.adapter.read_outputs.return_value = [
            Vehicle(veh_id="veh_1", soc=0.5, time=1.0),
            Battery(veh_id="veh_1", capacity=100),
        ]

        response = worker.read_outputs(None)

        assert response == Response(
            success=True,
            payload=[
                {"_type": "Vehicle", "veh_id": "veh_1", "soc": 0.5, "time": 1.0},
                {"_type": "Battery", "veh_id": "veh_1", "capacity": 100},
            ],
        )


class TestOnMessage:
    def _call(self, worker, operation):
        mock_ch = MagicMock()
        mock_props = MagicMock()
        body = json.dumps(Message(operation).to_dict())
        worker._on_message(mock_ch, MagicMock(), mock_props, body)
        return Response.from_dict(
            json.loads(mock_ch.basic_publish.call_args.kwargs["body"])
        )

    def test_dispatches_known_command(self, worker):
        reply = self._call(worker, Operation.INITIALIZE)
        assert reply == Response(success=True)

    def test_wraps_adapter_exception(self, worker):
        worker.adapter.initialize.side_effect = RuntimeError("adapter failed")
        reply = self._call(worker, Operation.INITIALIZE)
        assert reply == Response(success=False, error="adapter failed")


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
        self._setup_reply(worker, Response(success=True), "fixed-corr")
        with patch("adapter.adapter_worker.uuid.uuid4", return_value="fixed-corr"):
            worker.register()  # should not raise

    def test_rejected_raises(self, worker):
        self._setup_reply(
            worker, Response(success=False, error="timestep mismatch"), "fixed-corr"
        )
        with patch("adapter.adapter_worker.uuid.uuid4", return_value="fixed-corr"):
            with pytest.raises(RuntimeError, match="timestep mismatch"):
                worker.register()

    def test_timeout_raises(self, worker):
        worker.channel.basic_consume.return_value = "tag"
        with pytest.raises(TimeoutError):
            worker.register(timeout=0.0)
