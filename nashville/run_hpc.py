import argparse
import os
import shlex
import subprocess
import time

from dotenv import load_dotenv
from nashville.run_simulation import worker_process
from nashville.specs.model_spec import MODEL_SPECS
from state_memory import StateMemory
from supervisory import RabbitMQClient
from supervisory import SupervisoryModel

DEFAULT_REGISTRATION_TIMEOUT = 120.0
SQUEUE_POLL_INTERVAL_SECONDS = 10.0


def submit_worker_job(spec_name: str, log_dir: str) -> str:
    job_name = f"worker_{spec_name}"
    extra_args = shlex.split(os.environ.get("HPC_SBATCH_ARGS", ""))
    result = subprocess.run(
        [
            "sbatch",
            "--parsable",
            f"--job-name={job_name}",
            "--nodes=1",
            "--ntasks=1",
            f"--output={os.path.join(log_dir, job_name)}_%j.out",
            f"--error={os.path.join(log_dir, job_name)}_%j.err",
            *extra_args,
            "--wrap",
            f"python -m nashville.run_hpc --worker {spec_name}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def wait_for_jobs(job_ids: list[str]):
    pending = set(job_ids)
    while pending:
        time.sleep(SQUEUE_POLL_INTERVAL_SECONDS)
        result = subprocess.run(
            ["squeue", "-h", "-j", ",".join(pending), "-o", "%i"],
            capture_output=True,
            text=True,
        )
        pending = set(result.stdout.split())


def run_worker(spec_name: str):
    load_dotenv()

    specs_by_name = {spec.name: spec for spec in MODEL_SPECS}
    if spec_name not in specs_by_name:
        raise ValueError(f"Unknown model spec {spec_name!r}")

    worker_process(
        host=os.environ.get("BROKER_HOST", "localhost"),
        port=int(os.environ.get("BROKER_PORT", 5672)),
        username=os.environ.get("BROKER_USER", "guest"),
        password=os.environ.get("BROKER_PASSWORD", "guest"),
        spec=specs_by_name[spec_name],
        registration_timeout=DEFAULT_REGISTRATION_TIMEOUT,
    )


def main():
    load_dotenv()

    host = os.environ.get("BROKER_HOST", "localhost")
    port = int(os.environ.get("BROKER_PORT", 5672))
    username = os.environ.get("BROKER_USER", "guest")
    password = os.environ.get("BROKER_PASSWORD", "guest")

    log_dir = os.environ.get("HPC_LOG_DIR", "logs")
    os.makedirs(log_dir, exist_ok=True)

    job_ids = [submit_worker_job(spec.name, log_dir) for spec in MODEL_SPECS]

    state_memory = StateMemory(
        db_url=os.environ["DATABASE_URL"],
    )

    rabbitmq = RabbitMQClient(
        host=host, port=port, username=username, password=password
    )

    try:
        SupervisoryModel(
            model_specs=MODEL_SPECS,
            state_memory=state_memory,
            rabbitmq_client=rabbitmq,
            max_global_time=float(os.environ.get("MAX_GLOBAL_TIME", 86_400)),
            registration_timeout=DEFAULT_REGISTRATION_TIMEOUT,
            response_timeout=300.0,
        ).run()
    except Exception:
        subprocess.run(["scancel", *job_ids], check=False)
        raise

    wait_for_jobs(job_ids)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--worker",
        metavar="SPEC_NAME",
        help="Run a single worker for the given model spec. Used internally by "
        "the sbatch job submitted for each worker; not meant to be passed by hand.",
    )
    args = parser.parse_args()

    if args.worker:
        run_worker(args.worker)
    else:
        main()
