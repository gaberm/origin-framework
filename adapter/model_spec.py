from __future__ import annotations
from dataclasses import dataclass, field
from datetime import timedelta
from .adapter import Adapter


@dataclass(frozen=True)
class ModelSpec:
    """Describes one model in a simulation; used by the supervisory model to
    schedule it.

    `name` is a unique name for the model and is used internally.

    `adapter` is the Adapter subclass that wraps the model.

    `timestep_length` is how far the model advances per step, e.g.
    timedelta(seconds=1).

    `params` are extra keyword arguments for the adapter's __init__, e.g.
    {"config": "path/to/config.json"}.

    `dependencies` names the models this model waits for before each step.
    By default they are inferred from the adapter's inputs; set it to
    override them, or to () to wait for none.

    `active_from` and `active_until` let a model run for only part of the run,
    both measured from the start of the run: it takes its first step at
    active_from and stops by active_until. By default it runs for the whole run.
    """

    name: str
    adapter: type[Adapter]
    timestep_length: timedelta
    params: dict = field(default_factory=dict)
    dependencies: tuple[str, ...] | None = None
    active_from: timedelta = timedelta(0)
    active_until: timedelta | None = None

    @property
    def timestep_seconds(self) -> float:
        return self.timestep_length.total_seconds()

    @property
    def timing(self) -> dict:
        """When and how the model steps, in seconds. A worker sends it when it
        registers, so the supervisor can check both use the same spec."""
        return {
            "timestep_length": self.timestep_seconds,
            "active_from": self.active_from.total_seconds(),
            "active_until": (
                None if self.active_until is None else self.active_until.total_seconds()
            ),
        }

    @property
    def routing_key(self) -> str:
        return self.name

    def __post_init__(self):
        self._validate_name()
        self._validate_adapter()
        self._validate_timestep_length()
        self._validate_dependencies()
        self._validate_active_window()

    def _validate_name(self):
        if not isinstance(self.name, str):
            raise TypeError(
                f"ModelSpec.name must be a string; got {type(self.name).__name__!r}"
            )

    def _validate_adapter(self):
        if not (isinstance(self.adapter, type) and issubclass(self.adapter, Adapter)):
            raise TypeError(
                f"ModelSpec.adapter must be an Adapter subclass; got {type(self.adapter).__name__!r}"
            )

    def _validate_timestep_length(self):
        if not isinstance(self.timestep_length, timedelta):
            raise TypeError(
                f"ModelSpec.timestep_length must be a datetime.timedelta, e.g. "
                f"timedelta(seconds=1); got {type(self.timestep_length).__name__!r}"
            )
        if self.timestep_length <= timedelta(0):
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

    def _validate_active_window(self):
        if not isinstance(self.active_from, timedelta):
            raise TypeError(
                f"ModelSpec.active_from must be a datetime.timedelta, e.g. "
                f"timedelta(hours=2); got {type(self.active_from).__name__!r}"
            )
        if self.active_until is not None and not isinstance(
            self.active_until, timedelta
        ):
            raise TypeError(
                f"ModelSpec.active_until must be a datetime.timedelta or None, e.g. "
                f"timedelta(hours=10); got {type(self.active_until).__name__!r}"
            )
        if self.active_from < timedelta(0):
            raise ValueError(
                f"ModelSpec.active_from must not be negative; got {self.active_from}"
            )
        if self.active_until is not None and self.active_until <= self.active_from:
            raise ValueError(
                f"ModelSpec.active_until {self.active_until} must be after "
                f"active_from {self.active_from}"
            )
