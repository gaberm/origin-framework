import pika


class BrokerConnectionError(ConnectionError):
    """The RabbitMQ broker could not be reached or refused the login."""


def connect(
    host: str, port: int, username: str, password: str, heartbeat: int
) -> pika.BlockingConnection:
    """Open a blocking connection to the broker.

    pika's own errors often print as a bare `AMQPConnectionError`, so a
    failure is re-raised as BrokerConnectionError naming the broker and the
    reason."""
    try:
        return pika.BlockingConnection(
            pika.ConnectionParameters(
                host=host,
                port=port,
                credentials=pika.PlainCredentials(username, password),
                heartbeat=heartbeat,
            )
        )
    # OSError: pika lets a failed hostname lookup (socket.gaierror) through.
    except (pika.exceptions.AMQPConnectionError, OSError) as error:
        if isinstance(error, pika.exceptions.ProbableAuthenticationError):
            hint = "Check BROKER_USER and BROKER_PASSWORD."
        else:
            hint = "Is the broker running?"
        raise BrokerConnectionError(
            f"Cannot connect to RabbitMQ at {host}:{port}: {_reason(error)}. {hint}"
        ) from error


def _reason(error: Exception) -> str:
    # A refused or unreachable socket arrives as
    # AMQPConnectionError(AMQPConnectorSocketConnectError(cause)), and only
    # the cause has a message.
    wrapped = error.args[0] if error.args else None
    cause = getattr(wrapped, "exception", None) or error
    return str(cause) or type(cause).__name__
