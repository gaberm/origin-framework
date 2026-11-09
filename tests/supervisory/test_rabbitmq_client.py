import json
import pytest
from unittest.mock import MagicMock, patch
from supervisory.comm.rabbitmq_client import RabbitMQClient
from supervisory.comm.messages import Message, Response, Registration, Operation


@pytest.fixture
def client():
    with patch("supervisory.comm.rabbitmq_client.pika.BlockingConnection"):
        return RabbitMQClient(run_id="run_test")


class TestRunScopedMessaging:
    def test_declares_run_scoped_exchange(self, client):
        client.channel.exchange_declare.assert_called_once_with(
            exchange="run_test.tasks", exchange_type="direct", auto_delete=True
        )

    def test_sends_to_run_scoped_exchange(self, client):
        client._send("sumo", Message(Operation.ADVANCE), lambda r: None)
        publish = client.channel.basic_publish.call_args.kwargs
        assert publish["exchange"] == "run_test.tasks"
        assert publish["routing_key"] == "sumo"

    def test_collects_registrations_from_run_scoped_queue(self, client):
        client.channel.basic_consume.return_value = "consumer-tag"
        with pytest.raises(TimeoutError):
            client.collect_registrations(lambda r: (True, None), expected=1, timeout=0.0)
        client.channel.queue_declare.assert_any_call(
            queue="run_test.worker_registration", durable=True, auto_delete=True
        )
        assert client.channel.basic_consume.call_args.args[0] == (
            "run_test.worker_registration"
        )


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


class TestCall:
    """Workers reply while the client waits, i.e. on process_data_events."""

    def _reply_while_waiting(self, client, responses_by_worker):
        def deliver_replies(time_limit):
            for call in client.channel.basic_publish.call_args_list:
                worker = call.kwargs["routing_key"]
                correlation_id = call.kwargs["properties"].correlation_id
                if worker in responses_by_worker and correlation_id in client.pending:
                    client._on_reply(
                        None,
                        None,
                        MagicMock(correlation_id=correlation_id),
                        json.dumps(responses_by_worker[worker].to_dict()),
                    )

        client.connection.process_data_events.side_effect = deliver_replies

    def test_call_returns_reply_payload(self, client):
        self._reply_while_waiting(client, {"sumo": Response(success=True, payload=10.0)})

        assert client.call("sumo", Operation.ADVANCE, timeout=1.0) == 10.0
        message = json.loads(client.channel.basic_publish.call_args.kwargs["body"])
        assert message == {"command": "advance", "payload": None}

    def test_call_all_sends_to_every_worker_before_waiting(self, client):
        self._reply_while_waiting(
            client, {"sumo": Response(success=True), "charging": Response(success=True)}
        )
        deliver_replies = client.connection.process_data_events.side_effect
        sent_before_waiting = []

        def count_sent_then_reply(time_limit):
            sent_before_waiting.append(client.channel.basic_publish.call_count)
            deliver_replies(time_limit)

        client.connection.process_data_events.side_effect = count_sent_then_reply

        client.call_all(
            {"sumo": "sumo", "charging": "charging"}, Operation.INITIALIZE, timeout=1.0
        )

        assert sent_before_waiting[0] == 2

    def test_call_all_returns_payloads_by_worker_name(self, client):
        self._reply_while_waiting(
            client,
            {
                "sumo_key": Response(success=True, payload=[{"id": 1}]),
                "charging_key": Response(success=True),
            },
        )
        replied = []

        payloads = client.call_all(
            {"sumo": "sumo_key", "charging": "charging_key"},
            Operation.INITIALIZE,
            timeout=1.0,
            on_reply=replied.append,
        )

        assert payloads == {"sumo": [{"id": 1}], "charging": None}
        assert sorted(replied) == ["charging", "sumo"]

    def test_failed_reply_raises(self, client):
        self._reply_while_waiting(
            client, {"sumo": Response(success=False, error="ValueError: bad input")}
        )

        with pytest.raises(
            RuntimeError, match="advance failed for 'sumo': ValueError: bad input"
        ):
            client.call("sumo", Operation.ADVANCE, timeout=1.0)

    def test_missing_reply_times_out_naming_worker(self, client):
        self._reply_while_waiting(client, {"sumo": Response(success=True)})

        with pytest.raises(
            TimeoutError, match=r"terminate timed out; no response from: \['charging'\]"
        ):
            client.call_all(
                {"sumo": "sumo", "charging": "charging"},
                Operation.TERMINATE,
                timeout=0.05,
            )


class TestCollectRegistrations:
    def _make_handler_invoker(self, registrations, channel=None):
        """Returns a basic_consume side_effect that immediately delivers canned messages."""

        def fake_consume(queue, handler):
            for reg in registrations:
                mock_ch = channel or MagicMock()
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

    def test_accepted_worker_gets_payload_rejected_does_not(self, client):
        channel = MagicMock()
        registrations = [
            Registration(name="worker_a", routing_key="rk"),
            Registration(name="unexpected", routing_key="rk"),
        ]
        client.channel.basic_consume.side_effect = self._make_handler_invoker(
            registrations, channel
        )

        client.collect_registrations(
            lambda r: (r.name == "worker_a", None),
            expected=1,
            accepted_payload={"start_time": None},
        )

        replies = [
            json.loads(call.kwargs["body"]) for call in channel.basic_publish.call_args_list
        ]
        assert [reply["payload"] for reply in replies] == [{"start_time": None}, None]

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


def test_heartbeat_is_set_explicitly():
    from supervisory.comm.messages import DEFAULT_HEARTBEAT_SECONDS

    with patch("supervisory.comm.rabbitmq_client.pika.BlockingConnection"), patch(
        "supervisory.comm.rabbitmq_client.pika.ConnectionParameters"
    ) as parameters:
        RabbitMQClient(run_id="run_test")
        RabbitMQClient(run_id="run_test", heartbeat=30)
    heartbeats = [call.kwargs["heartbeat"] for call in parameters.call_args_list]
    assert heartbeats == [DEFAULT_HEARTBEAT_SECONDS, 30]


class TestRegistrationTimeoutStartsWithWorkers:
    def test_timeout_not_counted_before_workers_start(self, client):
        client.channel.basic_consume.return_value = "tag"
        started = iter([False, False, False, True])
        with pytest.raises(TimeoutError):
            client.collect_registrations(
                lambda r: (True, None),
                expected=1,
                timeout=0.0,
                workers_started=lambda: next(started),
            )
        # Waited (processing events) through every round before the start.
        assert client.connection.process_data_events.call_count >= 3

    def test_check_can_abort(self, client):
        client.channel.basic_consume.return_value = "tag"

        def job_failed():
            raise RuntimeError("worker job ended")

        with pytest.raises(RuntimeError, match="worker job ended"):
            client.collect_registrations(
                lambda r: (True, None), expected=1, workers_started=job_failed
            )
        client.channel.basic_cancel.assert_called_once_with("tag")
