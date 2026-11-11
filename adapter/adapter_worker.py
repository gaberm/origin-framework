import json
import time
import traceback
import uuid
from datetime import datetime, timedelta
import numpy as np
import pika
from base import validate_run_id
from comm import (
    DEFAULT_HEARTBEAT_SECONDS,
    Message,
    Operation,
    Response,
    Registration,
    registration_queue,
    task_exchange,
    task_queue,
    connect,
    records_to_payloads,
)
from .model_spec import ModelSpec


class AdapterWorker:
    """Runs one adapter and connects it to the SupervisoryModel over RabbitMQ.

    Builds the adapter from its ModelSpec, registers with the supervisor,
    then runs the supervisor's commands on the adapter and replies to each
    one until it is told to terminate.

    `host` and `port` are the address of the RabbitMQ broker.

    `username` and `password` are the broker login.

    `spec` is the ModelSpec of the model this worker runs.

    `run_id` is the run to join; it must match the supervisor's.

    `registration_timeout` is how many seconds to wait for the supervisor
    to accept the registration.

    `heartbeat` is the broker heartbeat timeout in seconds. The worker sends
    no heartbeats while the adapter is busy, so it must be longer than the
    adapter's slowest command.
    """

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        spec: ModelSpec,
        run_id: str,
        registration_timeout: float = 30.0,
        heartbeat: int = DEFAULT_HEARTBEAT_SECONDS,
    ):
        validate_run_id(run_id)

        self.run_id = run_id
        self.name = spec.name
        self.routing_key = spec.routing_key
        self.timing = spec.timing
        self.registration_timeout = registration_timeout

        self.adapter = self._build_adapter(spec, run_id)
        self._handlers = {
            "initialize": self.initialize,
            "write_inputs": self.write_inputs,
            "read_outputs": self.read_outputs,
            "advance": self.advance,
            "terminate": self.terminate,
        }

        self.queue_name = task_queue(run_id, spec.name)
        self.connection = connect(host, port, username, password, heartbeat)
        self.channel = self._open_task_channel()

    @staticmethod
    def _build_adapter(spec: ModelSpec, run_id: str):
        adapter = spec.adapter(
            name=spec.name,
            run_id=run_id,
            timestep_length=spec.timestep_length,
            active_from=spec.active_from,
            **spec.params,
        )
        return adapter

    def _open_task_channel(self):
        exchange = task_exchange(self.run_id)
        channel = self.connection.channel()
        channel.exchange_declare(
            exchange=exchange, exchange_type="direct", auto_delete=True
        )
        channel.queue_declare(queue=self.queue_name, durable=True, auto_delete=True)
        channel.queue_bind(
            queue=self.queue_name, exchange=exchange, routing_key=self.routing_key
        )
        channel.basic_qos(prefetch_count=1)
        return channel

    def register_with_supervisor(self):
        """Announce this worker to the supervisor and wait for it to accept.
        The supervisor's reply carries the run's start_time for the adapter."""
        response = self._request_registration()
        if not response.success:
            raise RuntimeError(f"registration rejected: {response.error}")

        start_time = response.payload["start_time"]
        if start_time is not None:
            self.adapter.start_time = datetime.fromisoformat(start_time)

    def _request_registration(self) -> Response:
        correlation_id = str(uuid.uuid4())
        reply_queue, responses = self._collect_replies(correlation_id)
        self._publish_registration(reply_queue, correlation_id)
        return self._wait_for_reply(responses)

    def _collect_replies(self, correlation_id: str) -> tuple[str, list[Response]]:
        """Open a private queue for the supervisor's reply to the registration.

        Returns the queue's name and a list that fills with the replies
        carrying `correlation_id`."""
        reply_queue = self.channel.queue_declare(queue="", exclusive=True).method.queue
        responses = []

        def on_reply(channel, method, properties, body):
            if properties.correlation_id == correlation_id:
                responses.append(Response.from_dict(json.loads(body)))

        self.channel.basic_consume(reply_queue, on_reply, auto_ack=True)
        return reply_queue, responses

    def _publish_registration(self, reply_queue: str, correlation_id: str):
        registration_queue_name = registration_queue(self.run_id)
        self.channel.queue_declare(
            queue=registration_queue_name, durable=True, auto_delete=True
        )
        registration = Registration(
            name=self.name, routing_key=self.routing_key, metadata=self.timing
        )
        self.channel.basic_publish(
            exchange="",
            routing_key=registration_queue_name,
            properties=pika.BasicProperties(
                reply_to=reply_queue, correlation_id=correlation_id
            ),
            body=json.dumps(registration.to_dict(), default=self._numpy_to_builtin),
        )

    def _wait_for_reply(self, responses: list[Response]) -> Response:
        deadline = time.time() + self.registration_timeout
        while not responses:
            if time.time() > deadline:
                raise TimeoutError("supervisory did not accept registration")
            self.connection.process_data_events(time_limit=1)
        return responses[0]

    def _on_message(self, ch, method, properties, body):
        """Run one command from the supervisor on the adapter and reply.

        A command that fails does not stop the worker; the error is sent back
        as a failed Response. The worker stops after a successful terminate.
        """
        command = None
        try:
            message = Message.from_dict(json.loads(body))
            command = message.command
            reply = self._handlers[command](message.payload)
            reply_body = json.dumps(reply.to_dict(), default=self._numpy_to_builtin)
        except Exception as error:
            traceback.print_exc()
            reply = Response(success=False, error=f"{type(error).__name__}: {error}")
            reply_body = json.dumps(reply.to_dict())
        ch.basic_publish(
            exchange="",
            routing_key=properties.reply_to,
            properties=pika.BasicProperties(correlation_id=properties.correlation_id),
            body=reply_body,
        )
        ch.basic_ack(delivery_tag=method.delivery_tag)
        if command == Operation.TERMINATE and reply.success:
            ch.stop_consuming()

    def initialize(self, payload) -> Response:
        outputs = self.adapter.initialize()
        return Response(
            success=True,
            payload={
                "current_time": self._current_time_s(),
                "records": records_to_payloads(outputs or []),
            },
        )

    def write_inputs(self, payload) -> Response:
        self.adapter.write_inputs(payload)
        return Response(success=True)

    def read_outputs(self, payload) -> Response:
        outputs = self.adapter.read_outputs()
        return Response(
            success=True,
            payload=records_to_payloads(outputs),
        )

    def advance(self, payload) -> Response:
        self.adapter.advance()
        return Response(success=True, payload=self._current_time_s())

    def _current_time_s(self) -> float:
        # The supervisory model keeps time as float seconds.
        current_time = self.adapter.current_time()
        if not isinstance(current_time, timedelta):
            raise TypeError(
                f"{type(self.adapter).__name__}.current_time() must be a "
                f"datetime.timedelta; got {type(current_time).__name__!r}"
            )
        return current_time.total_seconds()

    def terminate(self, payload) -> Response:
        outputs = self.adapter.terminate()
        if not outputs:
            return Response(success=True)
        return Response(
            success=True,
            payload=records_to_payloads(outputs),
        )

    def run(self):
        """Register with the supervisor, then run its commands until it
        terminates the worker. Blocks until then."""
        print(f"[{self.__class__.__name__}] Waiting for commands...")
        self.register_with_supervisor()
        self.channel.basic_consume(
            queue=self.queue_name, on_message_callback=self._on_message
        )
        self.channel.start_consuming()

    @staticmethod
    def _numpy_to_builtin(value):
        # Fallback if adapter returns numpy values
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, np.ndarray):
            return value.tolist()
        raise TypeError(
            f"Object of type {type(value).__name__} is not JSON serializable"
        )
