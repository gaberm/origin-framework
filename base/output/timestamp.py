from dataclasses import dataclass
from datetime import timedelta
from typing import ClassVar
from base.output.abstractable import Abstractable
from base.utils import declare_field, to_seconds


@dataclass(kw_only=True)
class Timestamp(Abstractable):
    """Base class for records that happen at a point in simulation time.

    `time_field` names the field that holds the time, in seconds or as a
    timedelta. The default is "time_s" but can be overridden.
    """

    time_field: ClassVar[str] = "time_s"

    def __post_init__(self):
        super().__post_init__()
        time = getattr(self, self.time_field)
        name = f"{type(self).__name__}.{self.time_field}"
        setattr(self, self.time_field, to_seconds(time, name))

    def __init_subclass__(cls, abstract: bool = False, **kwargs):
        if not abstract:
            declare_field(cls, cls.time_field, float | int | timedelta)
        super().__init_subclass__(abstract=abstract, **kwargs)
