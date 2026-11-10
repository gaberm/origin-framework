from __future__ import annotations
from datetime import timedelta, datetime
from typing import TYPE_CHECKING
from adapter import DataAdapter, ModelSpec
from base import TIME_TOLERANCE, Entity, Timestamp, as_list
from comm import Operation, RabbitMQClient, Registration, payloads_to_records
from supervisory.console_display import ConsoleDisplay
from supervisory.scheduling.scheduler import Scheduler

# Use TYPE_CHECKING to avoid circular imports during runtime
if TYPE_CHECKING:
    from state_memory import StateMemory


class SupervisoryModel:
    def __init__(
        self,
        model_specs: list[ModelSpec],
        state_memory: StateMemory,
        rabbitmq_client: RabbitMQClient,
        duration: timedelta,
        data_adapter: list[DataAdapter] = [],
        start_time: datetime | None = None,
        registration_timeout: float | int = 120.0,
        response_timeout: float | int = 60.0,
        shutdown_timeout: float | int = 10.0,
    ):
        self.model_names = [spec.name for spec in model_specs]
        self.routing_keys = {spec.name: spec.routing_key for spec in model_specs}
        self.output_types = {
            spec.name: spec.adapter.output_types for spec in model_specs
        }
        self.input_types = {spec.name: spec.adapter.input_types for spec in model_specs}
        self.timings = {spec.name: spec.timing for spec in model_specs}

        self.scheduler = Scheduler(model_specs, duration)
        self.state_memory = state_memory
        self.rabbitmq_client = rabbitmq_client

        self.data_adapter = data_adapter
        self.start_time = start_time
        self.registration_timeout = registration_timeout
        self.response_timeout = response_timeout
        self.shutdown_timeout = shutdown_timeout
        self.display = ConsoleDisplay()

        self._validate_model_names()
        self._validate_start_time()

    def _validate_model_names(self):
        """Model names must be unique, because RabbitMQ uses them as routing keys."""
        duplicate_names = sorted(
            {name for name in self.model_names if self.model_names.count(name) > 1}
        )
        if duplicate_names:
            raise ValueError(
                f"Model names must be unique; duplicated names: {duplicate_names}"
            )

    def _validate_start_time(self):
        if self.start_time is not None and not isinstance(self.start_time, datetime):
            raise TypeError(
                f"start_time must be a datetime.datetime, e.g. "
                f"datetime(1794, 9, 10); got {type(self.start_time).__name__!r}"
            )

    def await_workers(self):
        with self.display.wait_list(self.model_names, "Await Workers") as wait_list:

            def on_registered(name):
                wait_list.mark_done(name)

            try:
                self.rabbitmq_client.collect_registrations(
                    self._validate_registration,
                    len(self.model_names),
                    self.registration_timeout,
                    on_registered=on_registered,
                    accepted_payload={
                        "start_time": (
                            None
                            if self.start_time is None
                            else self.start_time.isoformat()
                        )
                    },
                )
            except TimeoutError as error:
                missing = sorted(set(self.model_names) - set(error.args[0]))
                raise RuntimeError(f"workers never registered: {missing}")

    def _validate_registration(self, registration: Registration):
        if registration.name not in self.timings:
            return False, f"unexpected worker '{registration.name}'"
        expected_timing = self.timings[registration.name]
        if registration.metadata != expected_timing:
            return False, (
                f"timing mismatch for '{registration.name}': worker sent "
                f"{registration.metadata}, supervisor expects {expected_timing}"
            )
        return True, None

    def setup_state_memory(self):
        self.state_memory.setup(self._record_types())

    def _record_types(self) -> list:
        """Flattened, de-duplicated output Record classes, with each entity's
        start observation after it."""
        types = []
        for value in self.output_types.values():
            for record_type in as_list(value):
                types.append(record_type)
                observation = getattr(record_type, "start_observation", None)
                if observation is not None:
                    types.append(observation)
        return list(dict.fromkeys(types))

    def reset_state_memory(self, drop_tables: bool = False):
        self.display.warning("Resetting state memory tables.")
        self.state_memory.reset_tables(drop_tables=drop_tables)

    def _call_worker(self, name: str, operation: Operation, payload=None):
        """Send one command to a model's worker and return its reply's payload."""
        return self.rabbitmq_client.call(
            self.routing_keys[name], operation, payload, timeout=self.response_timeout
        )

    def initialize_components(self):
        """Initialize every model and store the outputs they return, e.g.
        their initialize_only entities, before stepping."""
        with self.display.wait_list(
            self.model_names, "Initialize Components"
        ) as wait_list:
            replies = self.rabbitmq_client.call_all(
                self.routing_keys,
                Operation.INITIALIZE,
                timeout=self.response_timeout,
                on_reply=wait_list.mark_done,
            )
        for name, reply in replies.items():
            self._check_at_active_from(name, reply["current_time"])
            records = payloads_to_records(reply["records"], self.output_types[name])
            self._check_initialize_only(name, records)
            if records:
                self.state_memory.insert_records(records)

    def _check_at_active_from(self, name: str, current_time: float):
        """After initialize() the model must be where its first step starts,
        or every step after it would be off, e.g. when its data starts at a
        different date than the run."""
        active_from = self.scheduler.active_from[name]
        if abs(current_time - active_from) <= TIME_TOLERANCE:
            return

        current_time = timedelta(seconds=current_time)
        active_from = timedelta(seconds=active_from)
        if self.start_time:
            current_time = self.start_time + current_time
            active_from = self.start_time + active_from
        raise RuntimeError(
            f"'{name}' is at {current_time} after initialize(), but its first "
            f"step starts at {active_from} (ModelSpec.active_from)"
        )

    @staticmethod
    def _check_initialize_only(name: str, records: list):
        """Records from initialize must be flagged initialize_only, so the
        scheduler doesn't make the models that read them wait. A missing
        flag fails here, before the first step."""
        unflagged_record_types = sorted(
            {
                type(record).__name__
                for record in records
                if not getattr(record, "initialize_only", False)
            }
        )
        if unflagged_record_types:
            raise RuntimeError(
                f"'{name}' returned {unflagged_record_types} from initialize(), "
                f"which aren't initialize_only entities; set initialize_only = "
                f"True on them"
            )

    def load_data(self):
        records = []
        for adapter in self.data_adapter:
            # As workers get it when they register (see
            # AdapterWorker.register_with_supervisor), so the adapter can
            # convert its data's times to simulation time.
            adapter.start_time = self.start_time
            records += adapter.load_data()
        self.state_memory.insert_records(records)

    def write_inputs(self, name: str):
        inputs = self.state_memory.load_inputs(
            self.input_types[name], self.scheduler.time_window(name)
        )
        self._call_worker(name, Operation.WRITE_INPUTS, inputs)

    def advance(self, name: str):
        self.scheduler.complete_step(name, self._call_worker(name, Operation.ADVANCE))

    def read_outputs(self, name: str, record_dicts: list[dict]):
        records = payloads_to_records(record_dicts, self.output_types[name])
        self._check_record_times(name, records)
        self.state_memory.insert_records(records)

    def _check_record_times(self, name: str, records: list):
        """Check record times against the step the model just took."""
        step_end = self.scheduler.model_times[name]
        step_start = step_end - self.scheduler.timestep_lengths[name]
        for record in records:
            if isinstance(record, Timestamp):
                self._check_not_before_step(name, record, step_start, step_end)
            if isinstance(record, Entity):
                self._check_not_after_step(name, record, step_end)

    @staticmethod
    def _check_not_before_step(name: str, record, step_start: float, step_end: float):
        """A dependent model may already have read times before the step and
        would miss the record. Later times, e.g. a forecast, wait for readers."""
        time = getattr(record, record.time_field)
        if time < step_start - TIME_TOLERANCE:
            raise RuntimeError(
                f"'{name}' returned {type(record).__name__} with "
                f"{record.time_field}={time} s, before the step it just took "
                f"[{step_start}, {step_end}] s"
            )

    @staticmethod
    def _check_not_after_step(name: str, record, step_end: float):
        """Entities may be reported after they start or end, e.g. a battery
        once its trip is known, but not before."""
        for time_field in ("start_s", "end_s"):
            time = getattr(record, time_field)
            if time is not None and time > step_end + TIME_TOLERANCE:
                raise RuntimeError(
                    f"'{name}' returned {type(record).__name__} with "
                    f"{time_field}={time} s, after the end of the step it just "
                    f"took ({step_end} s)"
                )

    def terminate(self):
        try:
            replies = self.rabbitmq_client.call_all(
                self.routing_keys, Operation.TERMINATE, timeout=self.response_timeout
            )
            # Models may hand back final outputs; store them before the
            # connection closes.
            for name, record_dicts in replies.items():
                if record_dicts:
                    self.read_outputs(name, record_dicts)
        except BaseException:
            self._shut_down_after_error()
            raise
        self.state_memory.close_conn()
        self.display.end_run()

    def run(self):
        self.display.start_run()
        try:
            self._run_steps()
        except BaseException:
            # BaseException, so Ctrl+C also stops the workers.
            self._shut_down_after_error()
            raise
        self.terminate()

    def _shut_down_after_error(self):
        try:
            self.rabbitmq_client.call_all(
                self.routing_keys, Operation.TERMINATE, timeout=self.shutdown_timeout
            )
        except TimeoutError as error:
            self.display.warning(f"Workers did not confirm termination: {error}")
        except Exception as error:
            self.display.warning(f"Could not terminate workers: {error!r}")
        try:
            self.state_memory.close_conn()
        except Exception as error:
            self.display.warning(f"Could not close state memory cleanly: {error!r}")

    def _run_steps(self):
        self.display.print_dependencies(self.scheduler.dependencies)
        self.await_workers()
        self.setup_state_memory()
        self.load_data()
        self.initialize_components()
        with self.display.run_dashboard(self.scheduler.duration) as dashboard:
            while (name := self.scheduler.select_next()) is not None:
                self.write_inputs(name)
                self.advance(name)
                self.read_outputs(name, self._call_worker(name, Operation.READ_OUTPUTS))
                dashboard.update(
                    max(self.scheduler.model_times.values()), self.scheduler.model_times
                )
            dashboard.update(self.scheduler.duration, self.scheduler.model_times)
        self.display.print_final_model_times(self.scheduler.model_times)
