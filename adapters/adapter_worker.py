import pika
import json
from adapters.model_adapter import ModelAdapter
from supervisory.comm.commands import Message, Response


class AdapterWorker:
    @classmethod
    def from_config(cls, model, config) -> "AdapterWorker":
        return cls(
            host=config.rabbitmq.host,
            port=config.rabbitmq.port,
            username=config.rabbitmq.username,
            password=config.rabbitmq.password,
            routing_key=config.models[model].routing_key,
            queue_name=config.models[model].queue_name,
            adapter=ModelAdapter._registry[config.models[model].adapter].from_config(
                config.models[model]
            ),
        )

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        routing_key: str,
        queue_name: str,
        adapter: ModelAdapter,
    ):
        self.connection = pika.BlockingConnection(
            pika.ConnectionParameters(
                host=host,
                port=port,
                credentials=pika.PlainCredentials(username, password),
            )
        )
        self.channel = self.connection.channel()
        self.channel.exchange_declare(
            exchange="tasks", exchange_type="direct", durable=True
        )
        self.channel.queue_declare(queue=queue_name, durable=True)
        self.channel.queue_bind(
            queue=queue_name, exchange="tasks", routing_key=routing_key
        )
        self.channel.basic_qos(prefetch_count=1)
        self.channel.basic_consume(
            queue=queue_name, on_message_callback=self._on_message
        )
        self.adapter = adapter

    def _on_message(self, ch, method, properties, body):
        message = Message.from_dict(json.loads(body))

        handlers = {
            "initialize": self.initialize,
            "write_inputs": self.write_inputs,
            "read_outputs": self.read_outputs,
            "advance": self.advance,
            "terminate": self.terminate,
        }

        handler = handlers.get(message.command)
        if handler is None:
            reply = Response(success=False, error=f"Unknown command: {message.command}")
        else:
            try:
                reply = handler(message.payload)
            except Exception as e:
                reply = Response(success=False, error=str(e))

        ch.basic_publish(
            exchange="",
            routing_key=properties.reply_to,
            properties=pika.BasicProperties(correlation_id=properties.correlation_id),
            body=json.dumps(reply.to_dict()),
        )
        ch.basic_ack(delivery_tag=method.delivery_tag)

    def initialize(self, payload):
        self.adapter.initialize()
        return Response(success=True)

    def write_inputs(self, payload):
        inputs = self.adapter.InputType.from_dict(payload)
        self.adapter.write_inputs(inputs)
        return Response(success=True)

    def read_outputs(self, payload):
        outputs = self.adapter.read_outputs()
        return Response(success=True, payload=outputs.to_dict())

    def advance(self, payload):
        self.adapter.advance(payload)
        return Response(success=True)

    def terminate(self, payload):
        self.adapter.terminate()
        return Response(success=True)

    def run(self):
        print(f"[{self.__class__.__name__}] Waiting for commands...")
        self.channel.start_consuming()
