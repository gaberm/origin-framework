from __future__ import annotations
import dataclasses
import time
from datetime import timedelta
from typing import TYPE_CHECKING
from base import ModelSpec, as_list
from supervisory.comm.rabbitmq_client import RabbitMQClient, Response
from supervisory.comm.messages import Registration
from supervisory.console_display import ConsoleDisplay
from supervisory.scheduling.scheduler import Scheduler
from supervisory.geo.cell_assigner import assign_cells
from supervisory.geo.coords_converter import convert_coords

# Use TYPE_CHECKING to avoid circular imports during runtime
if TYPE_CHECKING:
    from state_memory import StateMemory


class SupervisoryModel:
    def __init__(
        self,
        model_specs: list[ModelSpec],
        state_memory: StateMemory,
        rabbitmq_client: RabbitMQClient,
        max_global_time: timedelta,
        registration_timeout: float | int = 120.0,
        response_timeout: float | int = 60.0,
        data_adapters: list = None,
        shutdown_timeout: float | int = 10.0,
        workers_started=None,
    ):
        self.model_names = [spec.name for spec in model_specs]
        self.routing_keys = {spec.name: spec.routing_key for spec in model_specs}
        self.output_types = {
            spec.name: spec.adapter.output_types for spec in model_specs
        }
        self.constant_types = {
            spec.name: getattr(spec.adapter, "constant_types", None)
            for spec in model_specs
        }
        self.input_types = {spec.name: spec.adapter.input_types for spec in model_specs}
        self.scheduler = Scheduler(model_specs, max_global_time)
        self.state_memory = state_memory
        self.rabbitmq_client = rabbitmq_client
        self.data_adapters = data_adapters or []
        self.registration_timeout = registration_timeout
        self.response_timeout = response_timeout
        # How long a failed run waits for workers to confirm termination.
        self.shutdown_timeout = shutdown_timeout
        # Optional check that the workers' processes are running; the
        # registration timeout only starts then (see collect_registrations).
        self.workers_started = workers_started
        self.display = ConsoleDisplay()

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
                )
            except TimeoutError as error:
                missing = sorted(set(self.model_names) - set(error.args[0]))
                raise RuntimeError(f"workers never registered: {missing}")

    def _validate_registration(self, registration: Registration):
        if registration.name not in self.scheduler.timestep_lengths:
            return False, f"unexpected worker '{registration.name}'"
        if (registration.metadata or {}).get(
            "timestep_length"
        ) != self.scheduler.timestep_lengths[registration.name]:
            return False, f"timestep mismatch for '{registration.name}'"
        return True, None

    def setup_state_memory(self):
        self.state_memory.setup(self._record_types())

    def _record_types(self) -> list:
        """Flattened, de-duplicated Record classes across outputs and constants."""
        types = []
        for value in list(self.output_types.values()) + list(
            self.constant_types.values()
        ):
            if value is not None:
                types += as_list(value)
        return list(dict.fromkeys(types))

    def reset_state_memory(self, drop_tables: bool = False):
        self.display.warning("Resetting state memory tables.")
        self.state_memory.reset_tables(drop_tables=drop_tables)

    def load_data(self):
        if not self.data_adapters:
            return
        names = [type(data_adapter).__name__ for data_adapter in self.data_adapters]
        with self.display.loading_data(names) as view:
            for data_adapter, name in zip(self.data_adapters, names):
                for dataset in data_adapter.load_data():
                    if dataset.h3_index:
                        dataset = assign_cells(dataset, resolution=9)
                    self.state_memory.insert_external_dataset(dataset)
                    view.record(name)
                view.mark_done(name)

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
        self._broadcast(
            self.rabbitmq_client.initialize, "initialize_components", dashboard=True
        )

    def read_constants(self):
        """Collect each model's time-invariant records once, before stepping."""
        responses = self._broadcast(
            self.rabbitmq_client.read_constants, "read_constants"
        )
        for name in self.model_names:
            constant_cls = self.constant_types.get(name)
            if constant_cls is None:
                continue
            if isinstance(constant_cls, (list, tuple)):
                raise NotImplementedError(
                    f"'{name}' declares multiple ConstantTypes; type-tagged "
                    "records are required to route them and are not yet supported."
                )
            payload = responses[name].payload
            if not payload:
                continue
            records = [constant_cls(**rec) for rec in payload]
            records = [convert_coords(rec) for rec in records]
            records = assign_cells(records, resolution=9)
            self.state_memory.insert_outputs(
                constant_cls, [dataclasses.asdict(rec) for rec in records]
            )

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
        self._store_outputs(name, response.payload)

    def _store_outputs(self, name: str, payload):
        records = self._deserialize(self.output_types[name], payload)
        records = [convert_coords(record) for record in records]
        records = assign_cells(records, resolution=9)
        by_type = {}
        for record in records:
            by_type.setdefault(type(record), []).append(dataclasses.asdict(record))
        for record_type, rows in by_type.items():
            self.state_memory.insert_outputs(record_type, rows)

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
                    self._store_outputs(name, response.payload)
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
        self.load_data()
        self.initialize_components()
        self.read_constants()
        with self.display.run_dashboard(self.scheduler.max_global_time) as dashboard:
            while (name := self.scheduler.select_next()) is not None:
                self.write_inputs(name)
                self.advance(name)
                self.read_outputs(name)
                dashboard.update(
                    max(self.scheduler.model_times.values()), self.scheduler.model_times
                )
            dashboard.update(self.scheduler.max_global_time, self.scheduler.model_times)
        self.display.print_final_model_times(self.scheduler.model_times)
