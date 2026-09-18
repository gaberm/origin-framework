import argparse
import os
import shlex
import subprocess
import time
from datetime import timedelta

from dotenv import load_dotenv
from nashville.run_simulation import worker_process
from nashville.specs.model_spec import MODEL_SPECS
from state_memory import StateMemory
from supervisory import RabbitMQClient
from supervisory import SupervisoryModel

DEFAULT_REGISTRATION_TIMEOUT = 120.0
SQUEUE_POLL_INTERVAL_SECONDS = 10.0
SCANCEL_WAIT_TIMEOUT_SECONDS = 60.0
SCANCEL_POLL_INTERVAL_SECONDS = 5.0


def submit_worker_job(spec_name: str, log_dir: str, run_id: str) -> str:
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
            f"python -m nashville.run_hpc --worker {spec_name} --run-id {run_id}",
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


def job_states(job_ids: list[str]) -> dict[str, tuple[str, str]] | None:
    """{job_id: (state, reason)} for the jobs still known to Slurm, or None if
    squeue failed for another reason (try again later)."""
    result = subprocess.run(
        ["squeue", "-h", "-j", ",".join(job_ids), "-o", "%i %T %r"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # squeue fails like this once none of the jobs is known any more.
        return {} if "Invalid job id" in result.stderr else None
    states = {}
    for line in result.stdout.splitlines():
        job_id, state, reason = (line.split(maxsplit=2) + ["", ""])[:3]
        states[job_id] = (state, reason)
    return states


class WorkerJobsStarted:
    """`workers_started` check for SupervisoryModel: True once every worker
    job runs, so the registration timeout doesn't include queue wait.

    Raises if a job leaves the queue before that: its worker can't register
    any more. Asks squeue at most every SQUEUE_POLL_INTERVAL_SECONDS."""

    def __init__(self, job_ids: list[str], log_dir: str):
        self.job_ids = job_ids
        self.log_dir = log_dir
        self._started = False
        self._next_poll = 0.0
        self._reported_pending = False

    def __call__(self) -> bool:
        if self._started or time.time() < self._next_poll:
            return self._started
        self._next_poll = time.time() + SQUEUE_POLL_INTERVAL_SECONDS
        states = job_states(self.job_ids)
        if states is None:
            return False
        ended = [job_id for job_id in self.job_ids if job_id not in states]
        if ended:
            raise RuntimeError(
                f"worker job(s) {ended} ended before their workers registered; "
                f"see the logs in {self.log_dir}"
            )
        pending = {
            job_id: reason
            for job_id, (state, reason) in states.items()
            if state != "RUNNING"
        }
        if pending and not self._reported_pending:
            print(f"Waiting for Slurm to start worker jobs: {pending}")
            self._reported_pending = True
        self._started = not pending
        return self._started


def cancel_jobs_and_wait(job_ids: list[str]):
    """Cancel jobs and block until squeue confirms they're gone, escalating
    to SIGKILL if they haven't cleared within SCANCEL_WAIT_TIMEOUT_SECONDS."""
    if not job_ids:
        return
    subprocess.run(["scancel", *job_ids], check=False)
    deadline = time.time() + SCANCEL_WAIT_TIMEOUT_SECONDS
    while time.time() < deadline:
        result = subprocess.run(
            ["squeue", "-h", "-j", ",".join(job_ids), "-o", "%i"],
            capture_output=True,
            text=True,
        )
        if not result.stdout.split():
            return
        time.sleep(SCANCEL_POLL_INTERVAL_SECONDS)
    subprocess.run(["scancel", "--signal=KILL", *job_ids], check=False)


def run_worker(spec_name: str, run_id: str):
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
        run_id=run_id,
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

    job_ids = []
    try:
        # Submitted only now: the workers need the run_id, and a failed
        # connection above then leaves no jobs behind.
        for spec in MODEL_SPECS:
            job_ids.append(
                submit_worker_job(spec.name, log_dir, state_memory.run_id)
            )
        SupervisoryModel(
            model_specs=MODEL_SPECS,
            state_memory=state_memory,
            rabbitmq_client=rabbitmq,
            # MAX_GLOBAL_TIME is in seconds.
            max_global_time=timedelta(
                seconds=float(os.environ.get("MAX_GLOBAL_TIME", 86_400))
            ),
            # Counted from when the worker jobs run, not from submission.
            registration_timeout=DEFAULT_REGISTRATION_TIMEOUT,
            workers_started=WorkerJobsStarted(job_ids, log_dir),
            response_timeout=300.0,
        ).run()
    except BaseException:
        # BaseException (not Exception) so Ctrl+C (KeyboardInterrupt) also
        # triggers cleanup; otherwise orphaned worker jobs keep running until
        # their walltime.
        cancel_jobs_and_wait(job_ids)
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
    parser.add_argument(
        "--run-id",
        help="Run the worker belongs to; required with --worker.",
    )
    args = parser.parse_args()

    if args.worker:
        if not args.run_id:
            parser.error("--worker requires --run-id")
        run_worker(args.worker, args.run_id)
    else:
        main()
