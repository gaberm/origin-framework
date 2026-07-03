import os
from dotenv import load_dotenv
from adapter import AdapterWorker
from state_memory import StateMemory
from supervisory import SupervisoryModel
from supervisory import RabbitMQClient
import multiprocessing
from nashville.specs.model_spec import MODEL_SPECS


def worker_process(
    host: str,
    port: int,
    username: str,
    password: str,
    spec,
    registration_timeout: float,
):
    adapter_instance = spec.adapter(
        name=spec.name, timestep_length=spec.timestep_length, **spec.params
    )
    worker = AdapterWorker(
        host=host,
        port=port,
        username=username,
        password=password,
        routing_key=spec.routing_key,
        queue_name=spec.queue_name,
        adapter=adapter_instance,
        registration_timeout=registration_timeout,
    )
    worker.run()


def main():
    load_dotenv()

    host = os.environ.get("BROKER_HOST", "localhost")
    port = int(os.environ.get("BROKER_PORT", 5672))
    username = os.environ.get("BROKER_USER", "guest")
    password = os.environ.get("BROKER_PASSWORD", "guest")
    registration_timeout = 120.0

    processes = [
        multiprocessing.Process(
            target=worker_process,
            args=(host, port, username, password, spec, registration_timeout),
        )
        for spec in MODEL_SPECS
    ]
    for p in processes:
        p.start()

    state_memory = StateMemory(
        db_url=os.environ["DATABASE_URL"],
    )

    rabbitmq = RabbitMQClient(
        host=host, port=port, username=username, password=password
    )

    SupervisoryModel(
        model_specs=MODEL_SPECS,
        state_memory=state_memory,
        rabbitmq_client=rabbitmq,
        max_global_time=float(os.environ.get("MAX_GLOBAL_TIME", 86_400)),
        registration_timeout=registration_timeout,
    ).run()

    for p in processes:
        p.join()


if __name__ == "__main__":
    main()
