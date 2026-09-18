import time
import uuid
import pika
import json
from typing import Any
from .messages import (
    DEFAULT_HEARTBEAT_SECONDS,
    Operation,
    Message,
    Response,
    Registration,
    registration_queue,
    task_exchange,
)


class RabbitMQClient:
    def __init__(
        self,
        run_id: str,
        host="localhost",
        port=5672,
        username="guest",
        password="guest",
        heartbeat: int = DEFAULT_HEARTBEAT_SECONDS,
    ):
        self.run_id = run_id
        self.exchange = task_exchange(run_id)
        self.connection = pika.BlockingConnection(
            pika.ConnectionParameters(
                host=host,
                port=port,
                credentials=pika.PlainCredentials(username, password),
                heartbeat=heartbeat,
            )
        )
        self.channel = self.connection.channel()
        # Must match the worker's declaration (see AdapterWorker).
        self.channel.exchange_declare(
            exchange=self.exchange, exchange_type="direct", auto_delete=True
        )
        result = self.channel.queue_declare(queue="", exclusive=True)
        self.reply_queue = result.method.queue
        self.pending = {}

        self.channel.basic_consume(
            queue=self.reply_queue, on_message_callback=self._on_reply, auto_ack=True
        )

    def collect_registrations(
        self,
        validate,
        expected: int,
        timeout: float = 30.0,
        on_registered=None,
        workers_started=None,
    ):
        """Block until `expected` distinct workers register and pass `validate`.
        validate(reg) -> (accepted: bool, error: str | None).
        on_registered(name), if given, is called each time a worker registers
        successfully, so callers can show live per-worker progress.
        workers_started(), if given, is polled while waiting; `timeout` only
        starts once it returns True, e.g. when queued batch jobs are running.
        It may raise to give up, e.g. when a job ended without registering.
        Returns {name: Registration}; raises TimeoutError(partial) on timeout."""
        # Must match the worker's declaration (see AdapterWorker.register).
        self.channel.queue_declare(
            queue=registration_queue(self.run_id), durable=True, auto_delete=True
        )
        registered: dict[str, Registration] = {}

        def handler(ch, method, props, body):
            reg = Registration.from_dict(json.loads(body))
            ok, error = validate(reg)
            if ok:
                registered[reg.name] = reg
                if on_registered is not None:
                    on_registered(reg.name)
            ch.basic_publish(
                exchange="",
                routing_key=props.reply_to,
                properties=pika.BasicProperties(correlation_id=props.correlation_id),
                body=json.dumps(Response(success=ok, error=error).to_dict()),
            )
            ch.basic_ack(method.delivery_tag)

        tag = self.channel.basic_consume(registration_queue(self.run_id), handler)
        deadline = None
        try:
            while len(registered) < expected:
                if deadline is None and (workers_started is None or workers_started()):
                    deadline = time.time() + timeout
                if deadline is not None and time.time() > deadline:
                    raise TimeoutError(registered)
                self.connection.process_data_events(time_limit=1)
        finally:
            self.channel.basic_cancel(tag)
        return registered

    def initialize(self, worker: str, on_ack):
        self._send(worker, Message(Operation.INITIALIZE), on_ack)

    def write_input(self, worker: str, input_data: Any, on_ack):
        self._send(worker, Message(Operation.WRITE_INPUTS, payload=input_data), on_ack)

    def read_constants(self, worker: str, on_ack):
        self._send(worker, Message(Operation.READ_CONSTANTS), on_ack)

    def read_outputs(self, worker: str, on_ack):
        self._send(worker, Message(Operation.READ_OUTPUTS), on_ack)

    def advance(self, worker: str, on_ack):
        self._send(worker, Message(Operation.ADVANCE), on_ack)

    def terminate(self, worker: str, on_ack):
        self._send(worker, Message(Operation.TERMINATE), on_ack)

    def _send(self, worker: str, message: Message, on_ack):
        correlation_id = str(uuid.uuid4())
        self.pending[correlation_id] = on_ack
        self.channel.basic_publish(
            exchange=self.exchange,
            routing_key=worker,
            properties=pika.BasicProperties(
                reply_to=self.reply_queue, correlation_id=correlation_id
            ),
            body=json.dumps(message.to_dict()),
        )

    def _on_reply(self, ch, method, props, body):
        callback = self.pending.pop(props.correlation_id, None)
        if callback:
            response = Response.from_dict(json.loads(body))
            callback(response)

    def run(self):
        self.connection.process_data_events(time_limit=None)

    def close(self):
        self.connection.close()
