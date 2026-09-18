import os
from datetime import timedelta
from dotenv import load_dotenv
from adapter import AdapterWorker
from state_memory import StateMemory
from supervisory import SupervisoryModel
from supervisory import RabbitMQClient
import multiprocessing
from nashville.specs.model_spec import MODEL_SPECS

WORKER_EXIT_TIMEOUT_SECONDS = 60.0


def worker_process(
    host: str,
    port: int,
    username: str,
    password: str,
    spec,
    run_id: str,
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
        run_id=run_id,
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

    # Created first: the workers need the run_id.
    state_memory = StateMemory(
        db_url=os.environ["DATABASE_URL"],
    )

    rabbitmq = RabbitMQClient(
        run_id=state_memory.run_id,
        host=host,
        port=port,
        username=username,
        password=password,
    )

    processes = [
        multiprocessing.Process(
            target=worker_process,
            args=(
                host,
                port,
                username,
                password,
                spec,
                state_memory.run_id,
                registration_timeout,
            ),
        )
        for spec in MODEL_SPECS
    ]
    for p in processes:
        p.start()

    try:
        SupervisoryModel(
            model_specs=MODEL_SPECS,
            state_memory=state_memory,
            rabbitmq_client=rabbitmq,
            # MAX_GLOBAL_TIME is in seconds.
            max_global_time=timedelta(
                seconds=float(os.environ.get("MAX_GLOBAL_TIME", 86_400))
            ),
            registration_timeout=registration_timeout,
        ).run()
    finally:
        # The supervisor asks the workers to stop, also after an error. Stop
        # the ones that don't, or Python would wait for them forever on exit.
        for p in processes:
            p.join(timeout=WORKER_EXIT_TIMEOUT_SECONDS)
            if p.is_alive():
                print(f"Worker process {p.pid} did not exit; terminating it.")
                p.terminate()
                p.join()


if __name__ == "__main__":
    main()
