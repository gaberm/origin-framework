from typing import Any


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, (list, tuple)) else [value]
