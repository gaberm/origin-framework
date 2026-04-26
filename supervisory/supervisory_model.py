from typing import Dict, List, Type
from adapters import BaseAdapter
from state_memory import StateMemory
from supervisory.comm.rabbitmq_client import RabbitMQClient
from supervisory.loaders import (
    BaseInputLoader,
    ChargingInputLoader,
    TransportationInputLoader,
)
from supervisory.space.cell_assigner import assign_cells
from supervisory.time import TimeRange
import logging
from tqdm import tqdm

logger = logging.getLogger(__name__)

INPUTS_LOADERS: Dict[str, Type[BaseInputLoader]] = {
    "charging": ChargingInputLoader,
    "transportation": TransportationInputLoader,
}


class SupervisoryModel:
    @classmethod
    def from_config(cls, config) -> "SupervisoryModel":
        model_names = list(config.models.keys())
        return cls(
            model_names=model_names,
            routing_keys={
                name: config.models[name].routing_key for name in model_names
            },
            output_types={
                name: BaseAdapter._registry[config.models[name].adapter].OutputType
                for name in model_names
            },
            input_loaders=cls._create_input_loaders(config, model_names),
            state_memory=StateMemory.from_config(config),
            max_global_time=config.simulation.max_global_time,
            rabbitmq_client=RabbitMQClient(**config.rabbitmq),
            adapter_time_steps={
                name: config.models[name].timestep_length for name in model_names
            },
        )

    def __init__(
        self,
        model_names: List[str],
        routing_keys: Dict[str, str],
        output_types: Dict[str, type],
        input_loaders: Dict[str, BaseInputLoader],
        state_memory: StateMemory,
        max_global_time: float,
        rabbitmq_client: RabbitMQClient,
        adapter_time_steps: Dict[str, float],
    ):
        model_names = model_names
        self.routing_keys = routing_keys
        self.output_types = output_types
        self.input_loaders = input_loaders
        self.state_memory = state_memory
        self.max_global_time = max_global_time
        self.rabbitmq_client = rabbitmq_client
        self.lagging_adapter_names: List[str] = []
        self._min_model_time = 0.0
        self.adapter_model_times: Dict[str, float] = {name: 0.0 for name in model_names}
        self.adapter_time_steps = adapter_time_steps

    @staticmethod
    def _create_input_loaders(config, model_names: list) -> Dict[str, BaseInputLoader]:
        input_loaders = {}
        for name in model_names:
            loader_class = INPUTS_LOADERS.get(name)
            if loader_class is None:
                raise ValueError(f"No input loader configured for adapter '{name}'.")
            input_loaders[name] = (
                loader_class.from_config(config)
                if hasattr(loader_class, "from_config")
                else loader_class()
            )
        return input_loaders

    def reset_state_memory(self, drop_tables: bool = False):
        logger.info("Resetting state memory tables.")
        self.state_memory.reset_tables(drop_tables=drop_tables)

    def _wait_for_all(self, responses: dict, expected: list, operation: str):
        while len(responses) < len(expected):
            self.rabbitmq_client.connection.process_data_events(time_limit=1)
        failed = {name: r.error for name, r in responses.items() if not r.success}
        if failed:
            details = "\n".join(f"  {name}: {error}" for name, error in failed.items())
            raise RuntimeError(f"{operation} failed for adapters:\n{details}")

    def initialize_adapters(self):
        responses = {}
        for name, routing_key in self.routing_keys.items():

            def on_ack(response, n=name):
                responses[n] = response

            self.rabbitmq_client.initialize(routing_key, on_ack=on_ack)
        self._wait_for_all(responses, list(self.routing_keys), "initialize_adapters")

    def write_inputs(self):
        responses = {}
        for name in self.lagging_adapter_names:
            time_interval = TimeRange(
                start_time=self.adapter_model_times[name],
                end_time=self.adapter_model_times[name] + self.adapter_time_steps[name],
            )
            inputs = self.input_loaders[name].load_input(
                self.state_memory.conn, time_interval
            )

            def on_ack(response, n=name):
                responses[n] = response

            self.rabbitmq_client.write_input(
                self.routing_keys[name], inputs.to_dict(), on_ack=on_ack
            )
        self._wait_for_all(responses, self.lagging_adapter_names, "write_inputs")

    def advance_components(self):
        responses = {}
        for name in self.lagging_adapter_names:

            def on_ack(response, n=name):
                responses[n] = response

            self.rabbitmq_client.advance(
                self.routing_keys[name], self.adapter_time_steps[name], on_ack=on_ack
            )
        self._wait_for_all(responses, self.lagging_adapter_names, "advance_components")
        for name in self.lagging_adapter_names:
            self.adapter_model_times[name] += self.adapter_time_steps[name]

    def read_outputs(self):
        responses = {}
        for name in self.lagging_adapter_names:

            def on_ack(response, n=name):
                responses[n] = response

            self.rabbitmq_client.read_outputs(self.routing_keys[name], on_ack=on_ack)
        self._wait_for_all(responses, self.lagging_adapter_names, "read_outputs")
        for name in self.lagging_adapter_names:
            output = self.output_types[name].from_dict(responses[name].payload)
            output = assign_cells(output, resolution=9)
            self.state_memory.insert_output(output)

    def find_lagging_adapters(self):
        next_step_time = min(
            self.adapter_model_times[name] + self.adapter_time_steps[name]
            for name in self.adapter_model_times
        )
        self.lagging_adapter_names = [
            name
            for name in self.adapter_model_times
            if self.adapter_model_times[name] + self.adapter_time_steps[name]
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

    def run(self):
        logger.info("Starting simulation run.")
        self.initialize_adapters()
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
