import dataclasses
import logging
import time
from tqdm import tqdm
from base import ModelSpec
from base.utils import as_list
from state_memory import StateMemory
from supervisory.comm.rabbitmq_client import RabbitMQClient, Response
from supervisory.comm.messages import Registration
from supervisory.scheduling.scheduler import Scheduler
from supervisory.space.cell_assigner import assign_cells
from supervisory.space.coords_converter import convert_coords

logger = logging.getLogger(__name__)


class SupervisoryModel:
    def __init__(
        self,
        model_specs: list[ModelSpec],
        state_memory: StateMemory,
        rabbitmq_client: RabbitMQClient,
        max_global_time: float,
        data_adapters: list = None,
    ):
        self.model_names = [spec.name for spec in model_specs]
        self.routing_keys = {spec.name: spec.routing_key for spec in model_specs}
        self.output_types = {
            spec.name: spec.adapter.output_types for spec in model_specs
        }
        self.constant_types = {
            spec.name: getattr(spec.adapter, "ConstantType", None)
            for spec in model_specs
        }
        self.input_types = {spec.name: spec.adapter.input_types for spec in model_specs}
        self.scheduler = Scheduler(model_specs, max_global_time)
        self.state_memory = state_memory
        self.rabbitmq_client = rabbitmq_client
        self.data_adapters = data_adapters or []
        logger.info("Derived dependencies: %s", self.scheduler.dependencies)

    def await_workers(self, timeout: float = 30.0):
        try:
            self.rabbitmq_client.collect_registrations(
                self._validate_registration, len(self.model_names), timeout
            )
        except TimeoutError as error:
            missing = sorted(set(self.model_names) - set(error.args[0]))
            raise RuntimeError(f"workers never registered: {missing}")
        logger.info("All %d workers registered.", len(self.model_names))

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
            types += as_list(value)
        return list(dict.fromkeys(types))

    def reset_state_memory(self, drop_tables: bool = False):
        logger.info("Resetting state memory tables.")
        self.state_memory.reset_tables(drop_tables=drop_tables)

    def load_data(self):
        for data_adapter in self.data_adapters:
            for dataset in data_adapter.load_data():
                if dataset.h3_index:
                    dataset = assign_cells(dataset, resolution=9)
                self.state_memory.insert_external_dataset(dataset)

    def _broadcast(self, send_fn, operation: str) -> dict:
        """Send an RPC to every worker and wait for all responses."""
        responses = {}
        for name, routing_key in self.routing_keys.items():

            def on_ack(response, n=name):
                responses[n] = response

            send_fn(routing_key, on_ack=on_ack)
        self._wait_for_all(responses, list(self.routing_keys), operation)
        return responses

    def initialize_components(self):
        self._broadcast(self.rabbitmq_client.initialize, "initialize_components")

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
            records = [constant_cls(**r) for r in payload]
            records = [convert_coords(r) for r in records]
            records = assign_cells(records, resolution=9)
            self.state_memory.insert_outputs(
                constant_cls, [dataclasses.asdict(r) for r in records]
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
        self.scheduler.sync_model_time(name, response.payload)

    def read_outputs(self, name: str):
        response = self._send_and_wait(
            name,
            "read_outputs",
            lambda ack: self.rabbitmq_client.read_outputs(
                self.routing_keys[name], on_ack=ack
            ),
        )
        records = self._deserialize(self.output_types[name], response.payload)
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
        self._broadcast(self.rabbitmq_client.terminate, "terminate")
        self.state_memory.close_conn()
        logger.info("Simulation run completed.")

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
        timeout: float = 30.0,
    ):
        deadline = time.time() + timeout
        while len(responses) < len(expected):
            if time.time() > deadline:
                missing = sorted(set(expected) - set(responses))
                raise TimeoutError(
                    f"{operation} timed out; no response from: {missing}"
                )
            self.rabbitmq_client.connection.process_data_events(time_limit=1)
        for name in expected:
            if not responses[name].success:
                raise RuntimeError(
                    f"{operation} failed for '{name}': {responses[name].error}"
                )

    def run(self):
        logger.info("Starting simulation run.")
        self.await_workers()
        self.setup_state_memory()
        self.load_data()
        self.initialize_components()
        self.read_constants()
        with tqdm(
            total=self.scheduler.max_global_time, desc="Simulation Progress"
        ) as progress:
            while (name := self.scheduler.select_next()) is not None:
                self.write_inputs(name)
                self.advance(name)
                self.read_outputs(name)
                progress.update(min(self.scheduler.model_times.values()) - progress.n)
        self._log_final_state()
        self.terminate()

    def _log_final_state(self):
        for name, model_time in self.scheduler.model_times.items():
            logger.info("Final model time for '%s': %s", name, model_time)
