import math
from datetime import timedelta
from numbers import Real
from base import Latest, ModelSpec
from base.utils import as_list
from supervisory.scheduling.time_window import TimeWindow

_EPS = 1e-9
# Seconds a reported model time may differ from the expected window end.
TIME_TOLERANCE = 1e-6


class Scheduler:
    def __init__(self, model_specs: list[ModelSpec], duration: timedelta):
        self._validate_duration(duration)
        # All times below are in seconds from the start of the run.
        self.duration = duration.total_seconds()
        self.model_names = [spec.name for spec in model_specs]
        self.timestep_lengths = {
            spec.name: spec.timestep_seconds for spec in model_specs
        }
        self.active_from = {
            spec.name: spec.active_from.total_seconds() for spec in model_specs
        }
        self.final_times = {
            spec.name: self._final_time(spec, duration) for spec in model_specs
        }
        self.model_times = dict(self.active_from)
        self.dependencies = self._derive_dependencies(model_specs)
        self._rank = self._compute_topo_ranks()

    @staticmethod
    def _validate_duration(duration: timedelta):
        if not isinstance(duration, timedelta):
            raise TypeError(
                f"duration must be a datetime.timedelta, e.g. "
                f"timedelta(hours=24); got {type(duration).__name__!r}"
            )
        if duration <= timedelta(0):
            raise ValueError(f"duration must be positive; got {duration}")

    @staticmethod
    def _final_time(spec: ModelSpec, duration: timedelta) -> float:
        """Time of the model's last whole step's end, by its active_until or the run's end."""
        active_until = duration if spec.active_until is None else spec.active_until
        if active_until > duration:
            raise ValueError(
                f"'{spec.name}'.active_until {active_until} is after the end of the "
                f"run ({duration})"
            )
        start = spec.active_from.total_seconds()
        step_count = math.floor(
            (active_until.total_seconds() - start + _EPS) / spec.timestep_seconds
        )
        if step_count < 1:
            raise ValueError(
                f"'{spec.name}' has no whole timestep of {spec.timestep_length} "
                f"between its active_from {spec.active_from} and {active_until}"
            )
        return start + step_count * spec.timestep_seconds

    def select_next(self) -> str | None:
        """Furthest-behind eligible model; ties broken by topological rank."""
        pending_models = [
            name
            for name in self.model_names
            if self.model_times[name] < self.final_times[name] - _EPS
        ]
        if not pending_models:
            return None
        eligible_models = [name for name in pending_models if self._eligible(name)]
        if not eligible_models:
            raise RuntimeError(
                "scheduler stalled: no eligible model to advance "
                f"(pending={pending_models}). This indicates a cycle without a "
                "designated lead — mark one coupling input Latest(...) or set "
                "ModelSpec.dependencies."
            )
        return min(
            eligible_models, key=lambda name: (self.model_times[name], self._rank[name])
        )

    def time_window(self, name: str) -> TimeWindow:
        return TimeWindow(self.model_times[name], self.time_window_end(name))

    def sync_model_time(self, name: str, time: float):
        self.model_times[name] = time

    def complete_step(self, name: str, reported_time) -> None:
        expected = self.time_window_end(name)
        if not isinstance(reported_time, Real) or not math.isclose(
            reported_time, expected, rel_tol=0, abs_tol=TIME_TOLERANCE
        ):
            raise RuntimeError(
                f"'{name}' reported time {reported_time!r} s after advancing; "
                f"expected {expected} s"
            )
        self.model_times[name] = expected

    def time_window_end(self, name: str) -> float:
        return self.model_times[name] + self.timestep_lengths[name]

    def _eligible(self, name: str) -> bool:
        """A model may advance only once every dependency has produced through its window end."""
        window_end = self.time_window_end(name)
        for dependency in self.dependencies.get(name, ()):
            required_time = self._required_time(dependency, window_end)
            if self.model_times[dependency] < required_time - _EPS:
                return False
        return True

    def _required_time(self, dependency: str, window_end: float) -> float:
        """The dependency's last step boundary before window_end. Its steps
        start at its active_from, and it never gets past its final time."""
        start = self.active_from[dependency]
        timestep = self.timestep_lengths[dependency]
        boundary = start + math.floor((window_end - start - _EPS) / timestep) * timestep
        return min(boundary, self.final_times[dependency])

    def _derive_dependencies(
        self, model_specs: list[ModelSpec]
    ) -> dict[str, tuple[str, ...]]:
        record_to_model = self._map_record_to_model(model_specs)
        dependencies = self._infer_dependencies(model_specs, record_to_model)
        self._apply_overrides(model_specs, dependencies)
        return {name: tuple(sorted(deps)) for name, deps in dependencies.items()}

    def _map_record_to_model(self, model_specs: list[ModelSpec]) -> dict[type, str]:
        """Map each output record type to the model that produces it.

        initialize_only entities are left out: they are all stored before
        the first step, so reading them waits for no model."""
        return {
            record: spec.name
            for spec in model_specs
            for record in as_list(spec.adapter.output_types)
            if not getattr(record, "initialize_only", False)
        }

    def _infer_dependencies(
        self, model_specs: list[ModelSpec], record_to_model: dict[type, str]
    ) -> dict[str, set]:
        """Infer gated dependency edges from Window input types."""
        dependencies: dict[str, set] = {spec.name: set() for spec in model_specs}
        for spec in model_specs:
            for input_cls in as_list(spec.adapter.input_types):
                if isinstance(getattr(input_cls, "read_policy", None), Latest):
                    continue
                for record in input_cls.records():
                    dependency = record_to_model.get(record)
                    if dependency is not None and dependency != spec.name:
                        dependencies[spec.name].add(dependency)
        return dependencies

    def _apply_overrides(
        self, model_specs: list[ModelSpec], dependencies: dict[str, set]
    ) -> None:
        """Replace inferred edges with explicit ModelSpec.dependencies overrides."""
        known_models = {spec.name for spec in model_specs}
        for spec in model_specs:
            if spec.dependencies is None:
                continue
            unknown_models = set(spec.dependencies) - known_models
            if unknown_models:
                raise ValueError(
                    f"'{spec.name}'.dependencies names unknown models: {sorted(unknown_models)}"
                )
            dependencies[spec.name] = set(spec.dependencies)

    def _compute_topo_ranks(self) -> dict[str, int]:
        ranks: dict[str, int] = {}
        in_progress: set = set()

        def depth(name: str) -> int:
            if name in ranks:
                return ranks[name]
            if name in in_progress:
                raise RuntimeError(
                    f"coupling cycle involving '{name}': models are mutually gated. "
                    "Mark one coupling input as Latest(...) to designate the lagged "
                    "direction, or set ModelSpec.dependencies explicitly."
                )
            in_progress.add(name)
            dep_names = self.dependencies.get(name, ())
            ranks[name] = (
                0 if not dep_names else 1 + max(depth(dep) for dep in dep_names)
            )
            in_progress.discard(name)
            return ranks[name]

        for name in self.model_names:
            depth(name)
        return ranks
