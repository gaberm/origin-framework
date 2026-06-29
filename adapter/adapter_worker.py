import dataclasses
import json
import time
import uuid
import pika
from adapter.adapter import Adapter
from supervisory.comm.messages import Message, Response, Registration


class AdapterWorker:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        routing_key: str,
        queue_name: str,
        adapter: Adapter,
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
        self.routing_key = routing_key
        self.name = adapter.name
        self.adapter = adapter
        self._handlers = {
            "initialize": self.initialize,
            "read_constants": self.read_constants,
            "write_inputs": self.write_inputs,
            "read_outputs": self.read_outputs,
            "advance": self.advance,
            "terminate": self.terminate,
        }

    def register(self, timeout: float = 30.0):
        self.channel.queue_declare(queue="worker_registration", durable=True)
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
            metadata={"timestep_length": self.adapter.timestep_length},
        )
        self.channel.basic_publish(
            exchange="",
            routing_key="worker_registration",
            properties=pika.BasicProperties(reply_to=reply, correlation_id=corr),
            body=json.dumps(reg.to_dict()),
        )
        deadline = time.time() + timeout
        while "resp" not in result:
            if time.time() > deadline:
                raise TimeoutError("supervisory did not accept registration")
            self.connection.process_data_events(time_limit=1)
        if not result["resp"].success:
            raise RuntimeError(f"registration rejected: {result['resp'].error}")

    def _on_message(self, ch, method, properties, body):
        message = Message.from_dict(json.loads(body))
        try:
            reply = self._handlers[message.command](message.payload)
        except KeyError:
            reply = Response(success=False, error=f"Unknown command: {message.command}")
        except Exception as e:
            reply = Response(success=False, error=str(e))
        ch.basic_publish(
            exchange="",
            routing_key=properties.reply_to,
            properties=pika.BasicProperties(correlation_id=properties.correlation_id),
            body=json.dumps(reply.to_dict()),
        )
        ch.basic_ack(delivery_tag=method.delivery_tag)

    def initialize(self, payload) -> Response:
        self.adapter.initialize()
        return Response(success=True)

    def write_inputs(self, payload) -> Response:
        self.adapter.write_inputs(payload)
        return Response(success=True)

    def read_constants(self, payload) -> Response:
        return Response(
            success=True,
            payload=[dataclasses.asdict(r) for r in self.adapter.read_constants()],
        )

    def read_outputs(self, payload) -> Response:
        outputs = self.adapter.read_outputs()
        if not isinstance(outputs, list):
            return Response(success=True, payload=outputs.to_dict())
        is_multi_type = isinstance(self.adapter.output_types, list)
        serialized = [
            (
                {"_type": type(output).__name__, **dataclasses.asdict(output)}
                if is_multi_type
                else dataclasses.asdict(output)
            )
            for output in outputs
        ]
        return Response(success=True, payload=serialized)

    def advance(self, payload) -> Response:
        return Response(success=True, payload=self.adapter.advance())

    def terminate(self, payload) -> Response:
        self.adapter.terminate()
        return Response(success=True)

    def run(self):
        print(f"[{self.__class__.__name__}] Waiting for commands...")
        self.register()
        self.channel.start_consuming()
