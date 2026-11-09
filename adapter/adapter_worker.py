import dataclasses
import json
import time
import traceback
import uuid
from datetime import datetime, timedelta
import numpy as np
import pika
from base import ModelSpec
from supervisory.comm.connection import connect
from supervisory.scheduling.scheduler import TIME_TOLERANCE
from supervisory.comm.messages import (
    DEFAULT_HEARTBEAT_SECONDS,
    Message,
    Operation,
    Response,
    Registration,
    registration_queue,
    task_exchange,
    task_queue,
)


def _to_builtin(value):
    """json.dumps fallback: adapters may return numpy values."""
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _require_timedelta(value, what: str) -> timedelta:
    """Adapters must report times as timedeltas."""
    if not isinstance(value, timedelta):
        raise TypeError(
            f"{what} must be a datetime.timedelta; got {type(value).__name__!r}"
        )
    return value


def _to_seconds(value, what: str) -> float:
    """Adapters use timedeltas; messages carry seconds."""
    return _require_timedelta(value, what).total_seconds()


class AdapterWorker:
    """Runs one adapter and connects it to the SupervisoryModel over RabbitMQ.

    Builds the adapter from its ModelSpec, registers with the supervisor,
    then runs the supervisor's commands on the adapter and replies to each
    one until it is told to terminate.
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
        if not isinstance(run_id, str):
            raise TypeError(f"run_id must be a str; got {type(run_id).__name__!r}")
        self.adapter = spec.adapter(
            name=spec.name,
            run_id=run_id,
            timestep_length=spec.timestep_length,
            **spec.params,
        )
        self.adapter.active_from = spec.active_from
        self.timing = spec.timing
        self.registration_timeout = registration_timeout
        self.run_id = run_id
        self.queue_name = task_queue(run_id, spec.queue_name)
        self.connection = connect(host, port, username, password, heartbeat)
        self.channel = self.connection.channel()
        self.channel.exchange_declare(
            exchange=task_exchange(run_id), exchange_type="direct", auto_delete=True
        )
        self.channel.queue_declare(
            queue=self.queue_name, durable=True, auto_delete=True
        )
        self.channel.queue_bind(
            queue=self.queue_name,
            exchange=task_exchange(run_id),
            routing_key=spec.routing_key,
        )
        self.channel.basic_qos(prefetch_count=1)
        self.routing_key = spec.routing_key
        self.name = spec.name
        self._handlers = {
            "initialize": self.initialize,
            "write_inputs": self.write_inputs,
            "read_outputs": self.read_outputs,
            "advance": self.advance,
            "terminate": self.terminate,
        }

    def register(self):
        self.channel.queue_declare(
            queue=registration_queue(self.run_id), durable=True, auto_delete=True
        )
        reply = self.channel.queue_declare(queue="", exclusive=True).method.queue
        corr = str(uuid.uuid4())
        result = {}

        def on_reply(ch, method, props, body):
            if props.correlation_id == corr:
                result["resp"] = Response.from_dict(json.loads(body))

        self.channel.basic_consume(reply, on_reply, auto_ack=True)
        reg = Registration(
            name=self.name,
            routing_key=self.routing_key,
            metadata=self.timing,
        )
        self.channel.basic_publish(
            exchange="",
            routing_key=registration_queue(self.run_id),
            properties=pika.BasicProperties(reply_to=reply, correlation_id=corr),
            body=json.dumps(reg.to_dict(), default=_to_builtin),
        )
        deadline = time.time() + self.registration_timeout
        while "resp" not in result:
            if time.time() > deadline:
                raise TimeoutError("supervisory did not accept registration")
            self.connection.process_data_events(time_limit=1)
        if not result["resp"].success:
            raise RuntimeError(f"registration rejected: {result['resp'].error}")
        start_time = result["resp"].payload["start_time"]
        if start_time is not None:
            self.adapter.start_time = datetime.fromisoformat(start_time)

    def _on_message(self, ch, method, properties, body):
        command = None
        try:
            message = Message.from_dict(json.loads(body))
            command = message.command
            reply = self._handlers[command](message.payload)
            reply_body = json.dumps(reply.to_dict(), default=_to_builtin)
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
        self._require_at_active_from()
        if not outputs:
            return Response(success=True)
        self._require_initialize_only(outputs)
        return Response(
            success=True,
            payload=self._pack_records(outputs, self.adapter.output_types),
        )

    def write_inputs(self, payload) -> Response:
        self.adapter.write_inputs(payload)
        return Response(success=True)

    def read_outputs(self, payload) -> Response:
        outputs = self.adapter.read_outputs()
        return Response(
            success=True,
            payload=self._pack_records(outputs, self.adapter.output_types),
        )

    def _require_at_active_from(self):
        """After initialize() the model must be where its first step starts,
        or every step after it would be off, e.g. when its data starts at a
        different date than the run."""
        adapter_name = type(self.adapter).__name__
        current_time = _require_timedelta(
            self.adapter.current_time(), f"{adapter_name}.current_time()"
        )
        active_from = self.adapter.active_from
        if abs((current_time - active_from).total_seconds()) <= TIME_TOLERANCE:
            return

        start_time = self.adapter.start_time
        if start_time:
            current_time = start_time + current_time
            active_from = start_time + active_from
        raise ValueError(
            f"{adapter_name} is at {current_time} after initialize(), but its "
            f"first step starts at {active_from} (ModelSpec.active_from)"
        )

    def _require_initialize_only(self, records):
        """Records from initialize must be flagged initialize_only, so the
        scheduler doesn't make the models that read them wait. A missing
        flag fails here, before the first step."""
        records = records if isinstance(records, list) else [records]
        unflagged_record_types = sorted(
            {
                type(record).__name__
                for record in records
                if not getattr(record, "initialize_only", False)
            }
        )
        if unflagged_record_types:
            raise ValueError(
                f"{type(self.adapter).__name__}.initialize() returned "
                f"{unflagged_record_types}, which aren't initialize_only entities; "
                f"set initialize_only = True on them"
            )

    @staticmethod
    def _pack_records(records, record_types) -> list[dict]:
        """Records as dicts, tagged with their `_type` when the adapter
        declares several record types, so the supervisor can tell them apart."""
        is_multi_type = isinstance(record_types, list)
        return [
            (
                {"_type": type(record).__name__, **dataclasses.asdict(record)}
                if is_multi_type
                else dataclasses.asdict(record)
            )
            for record in records
        ]

    def advance(self, payload) -> Response:
        self.adapter.advance()
        current_time = _to_seconds(
            self.adapter.current_time(),
            f"{type(self.adapter).__name__}.current_time()",
        )
        return Response(success=True, payload=current_time)

    def terminate(self, payload) -> Response:
        outputs = self.adapter.terminate()
        if not outputs:
            return Response(success=True)
        return Response(
            success=True,
            payload=self._pack_records(outputs, self.adapter.output_types),
        )

    def run(self):
        print(f"[{self.__class__.__name__}] Waiting for commands...")
        self.register()
        self.channel.basic_consume(
            queue=self.queue_name, on_message_callback=self._on_message
        )
        self.channel.start_consuming()
