import pytest
from adapter import DataAdapter


def test_load_data_is_required():
    class NoLoadData(DataAdapter):
        output_types = []

    with pytest.raises(TypeError, match="load_data"):
        NoLoadData(name="data")
