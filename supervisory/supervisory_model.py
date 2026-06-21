import dataclasses
import time
from base import Observation, Input, ModelSpec
from state_memory import StateMemory
from supervisory.comm.rabbitmq_client import RabbitMQClient
from supervisory.space.cell_assigner import assign_cells
from supervisory.space.coords_converter import convert_coords
from supervisory.time import TimeRange
import logging
from tqdm import tqdm

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
        self.model_names: list[str] = [spec.name for spec in model_specs]
        self.routing_keys: dict[str, str] = {
            spec.name: spec.routing_key for spec in model_specs
        }
        self.output_types: dict[str, type[Observation]] = {
            spec.name: spec.adapter.OutputType for spec in model_specs
        }
        self.constant_types: dict = {
            spec.name: getattr(spec.adapter, "ConstantType", None)
            for spec in model_specs
        }
        self.input_types: dict[str, type[Input] | list[type[Input]]] = {
            spec.name: spec.adapter.InputType for spec in model_specs
        }
        self.model_timestep_lengths: dict[str, float] = {
            spec.name: spec.timestep_length for spec in model_specs
        }
        self.adapter_model_times: dict[str, float] = {
            spec.name: 0.0 for spec in model_specs
        }

        self.state_memory = state_memory
        self.max_global_time = max_global_time
        self.rabbitmq_client = rabbitmq_client
        self.data_adapters: list = data_adapters or []

        self.lagging_adapter_names: list[str] = []
        self._min_model_time = 0.0

    def await_workers(self, timeout: float = 30.0):
        try:
            self.rabbitmq_client.collect_registrations(
                self._validate_registration, len(self.model_names), timeout
            )
        except TimeoutError as e:
            missing = sorted(set(self.model_names) - set(e.args[0]))
            raise RuntimeError(f"workers never registered: {missing}")
        logger.info("All %d workers registered.", len(self.model_names))

    def _validate_registration(self, reg):
        if reg.name not in self.model_timestep_lengths:
            return False, f"unexpected worker '{reg.name}'"
        if (reg.metadata or {}).get("timestep_length") != self.model_timestep_lengths[
            reg.name
        ]:
            return False, f"timestep mismatch for '{reg.name}'"
        return True, None

    def setup_state_memory(self):
        self.state_memory.setup(self._record_types())

    def _record_types(self) -> list:
        """Flattened, de-duplicated Record classes across outputs and constants."""
        types: list = []
        sources = list(self.output_types.values()) + list(self.constant_types.values())
        for value in sources:
            if value is None:
                continue
            types += list(value) if isinstance(value, (list, tuple)) else [value]
        seen, unique = set(), []
        for t in types:
            if t not in seen:
                seen.add(t)
                unique.append(t)
        return unique

    def reset_state_memory(self, drop_tables: bool = False):
        logger.info("Resetting state memory tables.")
        self.state_memory.reset_tables(drop_tables=drop_tables)

    def load_data(self):
        for data_adapter in self.data_adapters:
            for dataset in data_adapter.load_data():
                if dataset.h3_index:
                    dataset = assign_cells(dataset, resolution=9)
                self.state_memory.insert_external_dataset(dataset)

    def initialize_components(self):
        responses = {}
        for name, routing_key in self.routing_keys.items():

            def on_ack(response, n=name):
                responses[n] = response

            self.rabbitmq_client.initialize(routing_key, on_ack=on_ack)
        self._wait_for_all(responses, list(self.routing_keys), "initialize_components")

    def read_constants(self):
        """Collect each model's time-invariant records once, before stepping."""
        responses = {}
        for name, routing_key in self.routing_keys.items():

            def on_ack(response, n=name):
                responses[n] = response

            self.rabbitmq_client.read_constants(routing_key, on_ack=on_ack)
        self._wait_for_all(responses, list(self.routing_keys), "read_constants")
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
            records = assign_cells(records, resolution=9)
            rows = [dataclasses.asdict(r) for r in records]
            self.state_memory.insert_outputs(constant_cls, rows)

    def write_inputs(self):
        responses = {}
        for name in self.lagging_adapter_names:
            time_interval = TimeRange(
                start_time=self.adapter_model_times[name],
                end_time=self.adapter_model_times[name]
                + self.model_timestep_lengths[name],
            )
            inputs = self.state_memory.load_inputs(
                self.input_types[name], time_interval
            )

            def on_ack(response, n=name):
                responses[n] = response

            self.rabbitmq_client.write_input(
                self.routing_keys[name], inputs, on_ack=on_ack
            )
        self._wait_for_all(responses, self.lagging_adapter_names, "write_inputs")

    def advance_components(self):
        responses = {}
        for name in self.lagging_adapter_names:

            def on_ack(response, n=name):
                responses[n] = response

            self.rabbitmq_client.advance(
                self.routing_keys[name],
                self.model_timestep_lengths[name],
                on_ack=on_ack,
            )
        self._wait_for_all(responses, self.lagging_adapter_names, "advance_components")
        for name in self.lagging_adapter_names:
            self.adapter_model_times[name] += self.model_timestep_lengths[name]

    def read_outputs(self):
        responses = {}
        for name in self.lagging_adapter_names:

            def on_ack(response, n=name):
                responses[n] = response

            self.rabbitmq_client.read_outputs(self.routing_keys[name], on_ack=on_ack)
        self._wait_for_all(responses, self.lagging_adapter_names, "read_outputs")
        for name in self.lagging_adapter_names:
            output_cls = self.output_types[name]
            payload = responses[name].payload
            if isinstance(payload, list):
                records = [output_cls(**r) for r in payload]
            else:
                records = [output_cls.from_dict(payload)]
            records = assign_cells(records, resolution=9)
            records = convert_coords(records)
            rows = [dataclasses.asdict(r) for r in records]
            self.state_memory.insert_outputs(output_cls, rows)

    def find_lagging_adapters(self):
        next_step_time = min(
            self.adapter_model_times[name] + self.model_timestep_lengths[name]
            for name in self.adapter_model_times
        )
        self.lagging_adapter_names = [
            name
            for name in self.adapter_model_times
            if self.adapter_model_times[name] + self.model_timestep_lengths[name]
            == next_step_time
            <= self.max_global_time
        ]
        self._min_model_time = min(self.adapter_model_times.values())

    def terminate(self):
        responses = {}
        for name, routing_key in self.routing_keys.items():

            def on_ack(response, n=name):
                responses[n] = response

            self.rabbitmq_client.terminate(routing_key, on_ack=on_ack)
        self._wait_for_all(responses, list(self.routing_keys), "terminate")
        self.state_memory.close_conn()
        logger.info("Simulation run completed.")

    def _wait_for_all(
        self,
        responses: dict,
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
        self.find_lagging_adapters()
        with tqdm(total=self.max_global_time, desc="Simulation Progress") as pbar:
            while self.lagging_adapter_names:
                self.write_inputs()
                self.advance_components()
                self.read_outputs()
                self.find_lagging_adapters()
                pbar.update(self._min_model_time - pbar.n)
        self._log_final_state()
        self.terminate()

    def _log_final_state(self):
        for name, model_time in self.adapter_model_times.items():
            logger.info(f"Final model time for adapter '{name}': {model_time}")
