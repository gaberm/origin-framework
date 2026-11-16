import time
import uuid
import pika
import json
from typing import Any, Callable
from .connection import DEFAULT_HEARTBEAT_S, connect
from .messages import (
    Command,
    Request,
    Response,
    Registration,
    registration_queue,
    task_exchange,
)


class RabbitMQClient:
    """Manages the supervisory model's connection to RabbitMQ. Collects the
    adapter workers' registrations and sends them commands on behalf of the
    supervisor."""

    def __init__(
        self,
        run_id: str,
        host: str = "localhost",
        port: int = 5672,
        username: str = "guest",
        password: str = "guest",
        heartbeat: int = DEFAULT_HEARTBEAT_S,
    ):
        self.run_id = run_id
        self.exchange = task_exchange(run_id)
        self.response_callbacks = {}

        self.connection = connect(host, port, username, password, heartbeat)
        self.channel = self.connection.channel()
        # Must match the worker's declaration (see AdapterWorker).
        self.channel.exchange_declare(
            exchange=self.exchange, exchange_type="direct", auto_delete=True
        )

        self.reply_queue = self.channel.queue_declare(
            queue="", exclusive=True
        ).method.queue
        self.channel.basic_consume(
            queue=self.reply_queue, on_message_callback=self._on_reply, auto_ack=True
        )

    def collect_registrations(
        self,
        validate: Callable[[Registration], tuple[bool, str | None]],
        worker_count: int,
        timeout: float = 30.0,
        on_registered: Callable[[str], None] | None = None,
        accepted_payload: Any = None,
    ) -> dict[str, Registration]:
        """Wait until `worker_count` workers have registered and passed `validate`.
        On timeout, raises TimeoutError holding the registrations so far.

        `accepted_payload` is sent to each accepted worker, e.g. the run's
        start_time.

        `on_registered` is called with each accepted model's name.
        """
        # Must match the worker's declaration (see AdapterWorker).
        self.channel.queue_declare(
            queue=registration_queue(self.run_id), durable=True, auto_delete=True
        )
        registered: dict[str, Registration] = {}

        def handle_registration(channel, method, properties, body):
            registration = Registration.from_dict(json.loads(body))
            accepted, error = validate(registration)
            if accepted:
                registered[registration.model_name] = registration
                if on_registered is not None:
                    on_registered(registration.model_name)
            response = Response(
                success=accepted,
                payload=accepted_payload if accepted else None,
                error=error,
            )
            self._reply_to_registration(channel, properties, response)
            channel.basic_ack(method.delivery_tag)

        consumer_tag = self.channel.basic_consume(
            registration_queue(self.run_id), handle_registration
        )
        try:
            if not self._process_events_until(
                lambda: len(registered) >= worker_count, timeout
            ):
                raise TimeoutError(registered)
        finally:
            self.channel.basic_cancel(consumer_tag)
        return registered

    @staticmethod
    def _reply_to_registration(channel, properties, response: Response):
        channel.basic_publish(
            exchange="",
            routing_key=properties.reply_to,
            properties=pika.BasicProperties(correlation_id=properties.correlation_id),
            body=json.dumps(response.to_dict()),
        )

    def _process_events_until(self, is_done, timeout: float) -> bool:
        """Process broker events until is_done(). Returns False on timeout."""
        deadline = time.time() + timeout
        while not is_done():
            if time.time() > deadline:
                return False
            self.connection.process_data_events(time_limit=1)
        return True

    def call_worker(
        self,
        routing_key: str,
        command: Command,
        payload: Any = None,
        *,
        timeout: float,
    ) -> Any:
        """Send a command to a worker, wait for its reply and return the
        reply's payload. Raises like call_workers."""
        return self.call_workers(
            {routing_key: routing_key},
            command,
            payloads={routing_key: payload},
            timeout=timeout,
        )[routing_key]

    def call_workers(
        self,
        routing_keys: dict[str, str],
        command: Command,
        *,
        payloads: dict[str, Any] | None = None,
        timeout: float,
        on_reply=None,
    ) -> dict[str, Any]:
        """Send one command to several workers at once, wait for all replies
        and return each reply's payload by worker name.

        `routing_keys` are the routing keys of the workers receiving the
        `command`, by worker name.

        `payloads` is a dict with each worker's command payload, by worker
        name, e.g. {"traffic": [...], "charging": [...]}.

        `on_reply` is a callback called with the worker's name as each reply
        comes in.

        Raises a TimeoutError if a worker times out or a RuntimeError if a
        worker replies that the command failed.
        """
        responses: dict[str, Response] = {}
        for name, routing_key in routing_keys.items():

            def store_response(response, name=name):
                responses[name] = response
                if on_reply is not None:
                    on_reply(name)

            worker_payload = None if payloads is None else payloads[name]
            self._send_request(
                routing_key, Request(command, payload=worker_payload), store_response
            )
        self._process_events_until(lambda: len(responses) >= len(routing_keys), timeout)
        self._validate_responses(command, list(routing_keys), responses)
        return {name: response.payload for name, response in responses.items()}

    @staticmethod
    def _validate_responses(
        command: Command, names: list[str], responses: dict[str, Response]
    ):
        missing = sorted(set(names) - set(responses))
        if missing:
            raise TimeoutError(
                f"{command.value} timed out; no response from: {missing}"
            )
        for name in names:
            if not responses[name].success:
                raise RuntimeError(
                    f"{command.value} failed for '{name}': {responses[name].error}"
                )

    def _send_request(self, routing_key: str, request: Request, on_response):
        """Send `request` to the worker at `routing_key`.

        `on_response` is called with the worker's reply.
        """
        correlation_id = str(uuid.uuid4())
        self.response_callbacks[correlation_id] = on_response
        self.channel.basic_publish(
            exchange=self.exchange,
            routing_key=routing_key,
            properties=pika.BasicProperties(
                reply_to=self.reply_queue, correlation_id=correlation_id
            ),
            body=json.dumps(request.to_dict()),
        )

    def _on_reply(self, channel, method, properties, body):
        """Pass a worker's reply to its request's `on_response`; ignore
        unknown ones, e.g. replies that arrive after a timeout."""
        on_response = self.response_callbacks.pop(properties.correlation_id, None)
        if on_response:
            on_response(Response.from_dict(json.loads(body)))

    def close_connection(self):
        self.connection.close()
