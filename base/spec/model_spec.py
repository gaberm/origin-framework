from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from adapter.adapter import Adapter


@dataclass(frozen=True)
class ModelSpec:
    name: str
    adapter: type[Adapter]
    timestep_length: float
    params: dict = field(default_factory=dict)
    dependencies: tuple[str, ...] | None = None

    @property
    def routing_key(self) -> str:
        return self.name

    @property
    def queue_name(self) -> str:
        return f"{self.name}_queue"

    def __post_init__(self):
        self._validate_name()
        self._validate_adapter()
        self._validate_timestep_length()
        self._validate_dependencies()

    def _validate_name(self):
        if not isinstance(self.name, str):
            raise TypeError(
                f"ModelSpec.name must be a string; got {type(self.name).__name__!r}"
            )

    def _validate_adapter(self):
        from adapter.adapter import Adapter

        if not (isinstance(self.adapter, type) and issubclass(self.adapter, Adapter)):
            raise TypeError(
                f"ModelSpec.adapter must be an Adapter subclass; got {type(self.adapter).__name__!r}"
            )

    def _validate_timestep_length(self):
        if not isinstance(self.timestep_length, (float, int)):
            raise TypeError(
                f"ModelSpec.timestep_length must be a float or int; got {type(self.timestep_length).__name__!r}"
            )
        if self.timestep_length <= 0:
            raise ValueError(
                f"ModelSpec.timestep_length must be positive; got {self.timestep_length}"
            )

    def _validate_dependencies(self):
        if self.dependencies is not None:
            if not isinstance(self.dependencies, (tuple, list)):
                raise TypeError(
                    f"ModelSpec.dependencies must be a tuple or list; got {type(self.dependencies).__name__!r}"
                )
            for dependency in self.dependencies:
                if not isinstance(dependency, str):
                    raise TypeError(
                        f"ModelSpec.dependencies must be a list of strings; got {type(dependency).__name__!r}"
                    )
