import pika
import json
import uuid
from typing import Any
from .commands import Operation, Message, Response


class RabbitMQClient:
    @classmethod
    def from_config(cls, config) -> "RabbitMQClient":
        return cls(
            host=config.rabbitmq.host,
            port=config.rabbitmq.port,
            username=config.rabbitmq.username,
            password=config.rabbitmq.password,
        )

    def __init__(self, host="localhost", port=5672, username="guest", password="guest"):
        self.connection = pika.BlockingConnection(
            pika.ConnectionParameters(
                host=host,
                port=port,
                credentials=pika.PlainCredentials(username, password),
            )
        )
        self.channel = self.connection.channel()
        self.channel.exchange_declare(exchange="tasks", exchange_type="direct", durable=True)
        result = self.channel.queue_declare(queue="", exclusive=True)
        self.reply_queue = result.method.queue
        self.pending = {}

        self.channel.basic_consume(
            queue=self.reply_queue, on_message_callback=self._on_reply, auto_ack=True
        )

    def initialize(self, worker: str, on_ack):
        self._send(worker, Message(Operation.INITIALIZE), on_ack)

    def write_input(self, worker: str, input_data: Any, on_ack):
        self._send(worker, Message(Operation.WRITE_INPUTS, payload=input_data), on_ack)

    def read_outputs(self, worker: str, on_ack):
        self._send(worker, Message(Operation.READ_OUTPUTS), on_ack)

    def advance(self, worker: str, time_step: float, on_ack):
        self._send(worker, Message(Operation.ADVANCE, payload=time_step), on_ack)

    def terminate(self, worker: str, on_ack):
        self._send(worker, Message(Operation.TERMINATE), on_ack)

    def _send(self, worker: str, message: Message, on_ack):
        correlation_id = str(uuid.uuid4())
        self.pending[correlation_id] = on_ack
        self.channel.basic_publish(
            exchange="tasks",
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
