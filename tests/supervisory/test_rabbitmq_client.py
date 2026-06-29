import json
import pytest
from unittest.mock import MagicMock, patch
from supervisory.comm.rabbitmq_client import RabbitMQClient
from supervisory.comm.messages import Message, Response, Registration, Operation


@pytest.fixture
def client():
    with patch("supervisory.comm.rabbitmq_client.pika.BlockingConnection"):
        return RabbitMQClient()


class TestSendReplyRoundTrip:
    def test_callback_fired_with_response(self, client):
        received = []
        client._send("worker", Message(Operation.ADVANCE), lambda r: received.append(r))

        props = client.channel.basic_publish.call_args.kwargs["properties"]
        reply_body = json.dumps(Response(success=True).to_dict())
        mock_props = MagicMock(correlation_id=props.correlation_id)
        client._on_reply(None, None, mock_props, reply_body)

        assert received == [Response(success=True)]

    def test_callback_removed_from_pending_after_reply(self, client):
        client._send("worker", Message(Operation.ADVANCE), lambda r: None)
        assert len(client.pending) == 1

        props = client.channel.basic_publish.call_args.kwargs["properties"]
        mock_props = MagicMock(correlation_id=props.correlation_id)
        client._on_reply(
            None, None, mock_props, json.dumps(Response(success=True).to_dict())
        )

        assert len(client.pending) == 0

    def test_unknown_correlation_id_is_ignored(self, client):
        received = []
        client._send("worker", Message(Operation.ADVANCE), lambda r: received.append(r))

        mock_props = MagicMock(correlation_id="unknown-id")
        client._on_reply(
            None, None, mock_props, json.dumps(Response(success=True).to_dict())
        )

        assert received == []
        assert len(client.pending) == 1


class TestCollectRegistrations:
    def _make_handler_invoker(self, registrations):
        """Returns a basic_consume side_effect that immediately delivers canned messages."""

        def fake_consume(queue, handler):
            for reg in registrations:
                mock_ch = MagicMock()
                mock_method = MagicMock()
                mock_props = MagicMock()
                handler(mock_ch, mock_method, mock_props, json.dumps(reg.to_dict()))
            return "consumer-tag"

        return fake_consume

    def test_accepts_valid_registration(self, client):
        reg = Registration(name="worker_a", routing_key="rk")
        client.channel.basic_consume.side_effect = self._make_handler_invoker([reg])

        result = client.collect_registrations(lambda r: (True, None), expected=1)

        assert result == {"worker_a": reg}

    def test_rejected_registration_not_included(self, client):
        reg = Registration(name="unexpected", routing_key="rk")
        client.channel.basic_consume.side_effect = self._make_handler_invoker([reg])

        with pytest.raises(TimeoutError) as exc_info:
            client.collect_registrations(
                lambda r: (False, "unexpected"), expected=1, timeout=0.0
            )

        assert exc_info.value.args[0] == {}

    def test_raises_timeout_when_workers_missing(self, client):
        client.channel.basic_consume.return_value = "consumer-tag"

        with pytest.raises(TimeoutError):
            client.collect_registrations(
                lambda r: (True, None), expected=1, timeout=0.0
            )
