import pytest
from datetime import timedelta
from adapter.adapter import Adapter
from base import ModelSpec


class MinimalAdapter(Adapter):
    InputType = []
    OutputType = []

    def initialize(self):
        pass

    def read_constants(self):
        return []

    def write_inputs(self, inputs):
        pass

    def advance(self):
        pass

    def read_outputs(self):
        return []

    def terminate(self):
        pass


def test_wrong_name_type():
    with pytest.raises(TypeError, match="ModelSpec.name must be a string"):
        ModelSpec(name=123, adapter=MinimalAdapter, timestep_length=timedelta(seconds=1))


def test_wrong_adapter_type():
    with pytest.raises(
        TypeError, match="ModelSpec.adapter must be an Adapter subclass"
    ):
        ModelSpec(name="model", adapter="not_an_adapter", timestep_length=timedelta(seconds=1))


def test_wrong_timestep_type():
    with pytest.raises(
        TypeError, match="ModelSpec.timestep_length must be a datetime.timedelta"
    ):
        ModelSpec(name="model", adapter=MinimalAdapter, timestep_length="60")


def test_bare_number_timestep_rejected():
    with pytest.raises(TypeError, match="must be a datetime.timedelta"):
        ModelSpec(name="model", adapter=MinimalAdapter, timestep_length=60.0)


def test_non_positive_timestep():
    with pytest.raises(ValueError, match="ModelSpec.timestep_length must be positive"):
        ModelSpec(name="model", adapter=MinimalAdapter, timestep_length=timedelta(0))


def test_wrong_dependencies_type():
    with pytest.raises(TypeError, match="ModelSpec.dependencies must be a tuple or list"):
        ModelSpec(
            name="model", adapter=MinimalAdapter, timestep_length=timedelta(seconds=1), dependencies="sumo"
        )


def test_dependencies_non_string_elements():
    with pytest.raises(
        TypeError, match="ModelSpec.dependencies must be a list of strings"
    ):
        ModelSpec(
            name="model", adapter=MinimalAdapter, timestep_length=timedelta(seconds=1), dependencies=(1, 2)
        )


def test_valid_model_spec():
    spec = ModelSpec(name="model", adapter=MinimalAdapter, timestep_length=timedelta(seconds=1))
    assert spec.name == "model"
    assert spec.routing_key == "model"
    assert spec.queue_name == "model_queue"
    assert spec.dependencies is None


def test_timestep_seconds():
    spec = ModelSpec(
        name="model", adapter=MinimalAdapter, timestep_length=timedelta(minutes=1)
    )
    assert spec.timestep_seconds == 60.0
