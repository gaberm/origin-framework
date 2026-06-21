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

    @property
    def routing_key(self) -> str:
        return self.name

    @property
    def queue_name(self) -> str:
        return f"{self.name}_queue"
