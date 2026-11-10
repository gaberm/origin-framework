import time
import uuid
import pika
import json
from typing import Any
from .connection import DEFAULT_HEARTBEAT_SECONDS, connect
from .messages import (
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
        self.connection = connect(host, port, username, password, heartbeat)
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
        accepted_payload=None,
    ):
        """Block until `expected` distinct workers register and pass `validate`.
        validate(reg) -> (accepted: bool, error: str | None).
        accepted_payload, if given, is sent to each accepted worker in its
        reply, e.g. settings of the run the worker needs.
        on_registered(name), if given, is called each time a worker registers
        successfully, so callers can show live per-worker progress.
        Returns {name: Registration}; raises TimeoutError(partial) on timeout."""
        # Must match the worker's declaration (see
        # AdapterWorker.register_with_supervisor).
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
            reply = Response(
                success=ok, payload=accepted_payload if ok else None, error=error
            )
            ch.basic_publish(
                exchange="",
                routing_key=props.reply_to,
                properties=pika.BasicProperties(correlation_id=props.correlation_id),
                body=json.dumps(reply.to_dict()),
            )
            ch.basic_ack(method.delivery_tag)

        tag = self.channel.basic_consume(registration_queue(self.run_id), handler)
        deadline = time.time() + timeout
        try:
            while len(registered) < expected:
                if time.time() > deadline:
                    raise TimeoutError(registered)
                self.connection.process_data_events(time_limit=1)
        finally:
            self.channel.basic_cancel(tag)
        return registered

    def call(
        self, routing_key: str, operation: Operation, payload: Any = None, *, timeout: float
    ) -> Any:
        """Send one command to a worker, wait for its reply and return the
        reply's payload. Raises like call_all."""
        return self.call_all(
            {routing_key: routing_key}, operation, payload, timeout=timeout
        )[routing_key]

    def call_all(
        self,
        routing_keys: dict[str, str],
        operation: Operation,
        payload: Any = None,
        *,
        timeout: float,
        on_reply=None,
    ) -> dict[str, Any]:
        """Send one command to several workers at once, wait for all replies
        and return each reply's payload by worker name.
        routing_keys maps each worker's name to its routing key.
        on_reply(name), if given, is called as each reply comes in, so
        callers can show live per-worker progress.
        Raises TimeoutError naming the workers that didn't reply in time, and
        RuntimeError if a worker replied that the command failed."""
        responses: dict[str, Response] = {}
        for name, routing_key in routing_keys.items():

            def on_ack(response, name=name):
                responses[name] = response
                if on_reply is not None:
                    on_reply(name)

            self._send(routing_key, Message(operation, payload=payload), on_ack)
        self._wait_for_replies(responses, list(routing_keys), operation, timeout)
        return {name: response.payload for name, response in responses.items()}

    def _wait_for_replies(
        self,
        responses: dict[str, Response],
        names: list[str],
        operation: Operation,
        timeout: float,
    ):
        deadline = time.time() + timeout
        while len(responses) < len(names):
            if time.time() > deadline:
                missing = sorted(set(names) - set(responses))
                raise TimeoutError(
                    f"{operation.value} timed out; no response from: {missing}"
                )
            self.connection.process_data_events(time_limit=1)
        for name in names:
            if not responses[name].success:
                raise RuntimeError(
                    f"{operation.value} failed for '{name}': {responses[name].error}"
                )

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
