from __future__ import annotations
import dataclasses
import time
from datetime import timedelta, datetime
from typing import TYPE_CHECKING
from base import Entity, ModelSpec, Observation, Timestamp, as_list, refs
from supervisory.comm.rabbitmq_client import RabbitMQClient, Response
from supervisory.comm.messages import Registration
from supervisory.console_display import ConsoleDisplay
from supervisory.scheduling.scheduler import TIME_TOLERANCE, Scheduler

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
        start_time: datetime | None = None,
        registration_timeout: float | int = 120.0,
        response_timeout: float | int = 60.0,
        shutdown_timeout: float | int = 10.0,
        workers_started=None,
    ):
        self.model_names = [spec.name for spec in model_specs]
        self._validate_model_names()
        self.routing_keys = {spec.name: spec.routing_key for spec in model_specs}
        self.output_types = {
            spec.name: spec.adapter.output_types for spec in model_specs
        }
        self.input_types = {spec.name: spec.adapter.input_types for spec in model_specs}
        self.timings = {spec.name: spec.timing for spec in model_specs}
        self.scheduler = Scheduler(model_specs, duration)
        self._validate_start_time(start_time)
        # The calendar time of the run's start, if the run has one. Workers
        # get it when they register, so adapters can convert dates to run time.
        self.start_time = start_time
        self.state_memory = state_memory
        self.rabbitmq_client = rabbitmq_client
        self.registration_timeout = registration_timeout
        self.response_timeout = response_timeout
        # How long a failed run waits for workers to confirm termination.
        self.shutdown_timeout = shutdown_timeout
        # Optional check that the workers' processes are running; the
        # registration timeout only starts then (see collect_registrations).
        self.workers_started = workers_started
        self.display = ConsoleDisplay()

    def _validate_model_names(self):
        """Model names must be unique, because they are used as routing keys by RabbitMQ."""
        duplicate_names = sorted(
            {name for name in self.model_names if self.model_names.count(name) > 1}
        )
        if duplicate_names:
            raise ValueError(
                f"Model names must be unique; duplicated names: {duplicate_names}"
            )

    @staticmethod
    def _validate_start_time(start_time: datetime | None):
        if start_time is not None and not isinstance(start_time, datetime):
            raise TypeError(
                f"start_time must be a datetime.datetime, e.g. "
                f"datetime(1794, 9, 10); got {type(start_time).__name__!r}"
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
                    workers_started=self.workers_started,
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

    def _broadcast(self, send_fn, operation: str, dashboard: bool = False) -> dict:
        """Send an RPC to every worker and wait for all responses."""
        responses = {}
        for name, routing_key in self.routing_keys.items():

            def on_ack(response, n=name):
                responses[n] = response

            send_fn(routing_key, on_ack=on_ack)
        expected = list(self.routing_keys)
        if dashboard:
            self._wait_for_all_with_dashboard(responses, expected, operation)
        else:
            self._wait_for_all(responses, expected, operation)
        return responses

    def initialize_components(self):
        """Initialize every model and store the outputs they return, e.g.
        their initialize_only entities, before stepping."""
        responses = self._broadcast(
            self.rabbitmq_client.initialize, "initialize_components", dashboard=True
        )
        for name, response in responses.items():
            if response.payload:
                self._store_records(self.output_types[name], response.payload)

    def write_inputs(self, name: str):
        inputs = self.state_memory.load_inputs(
            self.input_types[name], self.scheduler.time_window(name)
        )
        self._send_and_wait(
            name,
            "write_inputs",
            lambda ack: self.rabbitmq_client.write_input(
                self.routing_keys[name], inputs, on_ack=ack
            ),
        )

    def advance(self, name: str):
        response = self._send_and_wait(
            name,
            "advance",
            lambda ack: self.rabbitmq_client.advance(
                self.routing_keys[name], on_ack=ack
            ),
        )
        self.scheduler.complete_step(name, response.payload)

    def read_outputs(self, name: str):
        response = self._send_and_wait(
            name,
            "read_outputs",
            lambda ack: self.rabbitmq_client.read_outputs(
                self.routing_keys[name], on_ack=ack
            ),
        )
        self._store_step_outputs(name, response.payload)

    def _store_step_outputs(self, name: str, payload):
        """Store the outputs of the step the model just took, once their
        times are checked."""
        records = self._deserialize(self.output_types[name], payload)
        self._check_record_times(name, records)
        self._insert_records(records)

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

    def _store_records(self, record_types, payload):
        self._insert_records(self._deserialize(record_types, payload))

    def _insert_records(self, records: list):
        by_type = {}
        for record in records:
            by_type.setdefault(type(record), []).append(dataclasses.asdict(record))
        for record_type in self._storage_order(list(by_type)):
            self.state_memory.insert_outputs(record_type, by_type[record_type])

    @staticmethod
    def _storage_order(record_types: list[type]) -> list[type]:
        """Each record after the entities it references, and observations
        after all entities, so foreign keys and start or end observations
        find their entity. Keeps the given order otherwise."""
        ordered = []

        def add(record_type, visiting):
            if record_type in ordered or record_type in visiting:
                return
            for target in refs(record_type).values():
                if target in record_types:
                    add(target, visiting | {record_type})
            ordered.append(record_type)

        entities = [
            record_type
            for record_type in record_types
            if not issubclass(record_type, Observation)
        ]
        for record_type in entities:
            add(record_type, frozenset())
        observations = [
            record_type
            for record_type in record_types
            if issubclass(record_type, Observation)
        ]
        return ordered + observations

    def _deserialize(self, output_cls, payload) -> list:
        if isinstance(output_cls, list):
            type_map = {record_cls.__name__: record_cls for record_cls in output_cls}
            return [
                type_map[row["_type"]](
                    **{key: val for key, val in row.items() if key != "_type"}
                )
                for row in payload
            ]
        if isinstance(payload, list):
            return [output_cls(**row) for row in payload]
        return [output_cls.from_dict(payload)]

    def terminate(self):
        try:
            responses = self._broadcast(self.rabbitmq_client.terminate, "terminate")
            # Models may hand back final outputs; store them before the
            # connection closes.
            for name, response in responses.items():
                if response.payload:
                    self._store_step_outputs(name, response.payload)
        except BaseException:
            self._shut_down_after_error()
            raise
        self.state_memory.close_conn()
        self.display.end_run()

    def _shut_down_after_error(self):
        responses = {}
        try:
            for name, routing_key in self.routing_keys.items():

                def on_ack(response, n=name):
                    responses[n] = response

                self.rabbitmq_client.terminate(routing_key, on_ack=on_ack)
            deadline = time.time() + self.shutdown_timeout
            while len(responses) < len(self.routing_keys) and time.time() < deadline:
                self.rabbitmq_client.connection.process_data_events(time_limit=1)
            missing = sorted(set(self.routing_keys) - set(responses))
            if missing:
                self.display.warning(f"Workers did not confirm termination: {missing}")
        except Exception as error:
            self.display.warning(f"Could not terminate workers: {error!r}")
        try:
            self.state_memory.close_conn()
        except Exception as error:
            self.display.warning(f"Could not close state memory cleanly: {error!r}")

    def _send_and_wait(self, name: str, operation: str, send):
        """Issue one RPC to a single worker and block until its response."""
        responses: dict = {}

        def on_ack(response):
            responses[name] = response

        send(on_ack)
        self._wait_for_all(responses, [name], operation)
        return responses[name]

    def _wait_for_all(
        self,
        responses: dict[str, Response],
        expected: list[str],
        operation: str,
        timeout: float | None = None,
    ):
        deadline = time.time() + (self.response_timeout if timeout is None else timeout)
        while len(responses) < len(expected):
            if time.time() > deadline:
                missing = sorted(set(expected) - set(responses))
                raise TimeoutError(
                    f"{operation} timed out; no response from: {missing}"
                )
            self.rabbitmq_client.connection.process_data_events(time_limit=1)
        self._check_responses(responses, expected, operation)

    def _wait_for_all_with_dashboard(
        self,
        responses: dict[str, Response],
        expected: list[str],
        operation: str,
    ):
        """Like _wait_for_all, but shows a live spinner per model, updated in
        place as each model's response comes in."""
        title = operation.replace("_", " ").title()
        deadline = time.time() + self.response_timeout
        with self.display.wait_list(expected, title) as wait_list:
            while len(responses) < len(expected):
                if time.time() > deadline:
                    missing = sorted(set(expected) - set(responses))
                    raise TimeoutError(
                        f"{operation} timed out; no response from: {missing}"
                    )
                self.rabbitmq_client.connection.process_data_events(time_limit=1)
                for name in responses:
                    wait_list.mark_done(name)
        self._check_responses(responses, expected, operation)

    def _check_responses(
        self,
        responses: dict[str, Response],
        expected: list[str],
        operation: str,
    ):
        for name in expected:
            if not responses[name].success:
                raise RuntimeError(
                    f"{operation} failed for '{name}': {responses[name].error}"
                )

    def run(self):
        self.display.start_run()
        try:
            self._run_steps()
        except BaseException:
            # BaseException, so Ctrl+C also stops the workers.
            self._shut_down_after_error()
            raise
        self.terminate()

    def _run_steps(self):
        self.display.print_dependencies(self.scheduler.dependencies)
        self.await_workers()
        self.setup_state_memory()
        self.initialize_components()
        with self.display.run_dashboard(self.scheduler.duration) as dashboard:
            while (name := self.scheduler.select_next()) is not None:
                self.write_inputs(name)
                self.advance(name)
                self.read_outputs(name)
                dashboard.update(
                    max(self.scheduler.model_times.values()), self.scheduler.model_times
                )
            dashboard.update(self.scheduler.duration, self.scheduler.model_times)
        self.display.print_final_model_times(self.scheduler.model_times)
