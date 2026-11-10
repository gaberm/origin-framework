import socket
import pika
import pytest
from unittest.mock import patch
from pika.adapters.utils.connection_workflow import AMQPConnectorSocketConnectError
from comm.connection import BrokerConnectionError, connect


def _connect_failing_with(error):
    with patch(
        "comm.connection.pika.BlockingConnection", side_effect=error
    ):
        with pytest.raises(BrokerConnectionError) as raised:
            connect("broker-host", 55672, "user", "password", heartbeat=600)
    assert raised.value.__cause__ is error
    return str(raised.value)


def test_refused_connection_names_broker_and_reason():
    # As pika 1.4 raises it: the socket error is nested and str() is empty.
    error = pika.exceptions.AMQPConnectionError(
        AMQPConnectorSocketConnectError(
            ConnectionRefusedError(111, "Connection refused")
        )
    )
    assert _connect_failing_with(error) == (
        "Cannot connect to RabbitMQ at broker-host:55672: "
        "[Errno 111] Connection refused. Is the broker running?"
    )


def test_unknown_host():
    error = socket.gaierror(-2, "Name or service not known")
    assert _connect_failing_with(error) == (
        "Cannot connect to RabbitMQ at broker-host:55672: "
        "[Errno -2] Name or service not known. Is the broker running?"
    )


def test_refused_login_points_at_credentials():
    error = pika.exceptions.ProbableAuthenticationError("ACCESS_REFUSED")
    message = _connect_failing_with(error)
    assert "ACCESS_REFUSED" in message
    assert message.endswith("Check BROKER_USER and BROKER_PASSWORD.")


def test_bare_error_falls_back_to_its_type():
    error = pika.exceptions.AMQPConnectionError()
    assert "broker-host:55672: AMQPConnectionError." in _connect_failing_with(error)
