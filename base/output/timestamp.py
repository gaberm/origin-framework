from dataclasses import dataclass
from datetime import timedelta
from numbers import Real
from typing import ClassVar
from base.output.abstractable import Abstractable


@dataclass(kw_only=True)
class Timestamp(Abstractable):
    """Base class for records that happen at a point in simulation time.

    `time_field` names the field that holds the time, in seconds or as a
    timedelta. The default is "time_s" but can be overridden.
    """

    time_field: ClassVar[str] = "time_s"

    def __post_init__(self):
        parent_post_init = getattr(super(), "__post_init__", None)
        if parent_post_init is not None:
            parent_post_init()
        self._validate_time()

    def _validate_time(self):
        time = getattr(self, self.time_field)
        if not isinstance(time, timedelta) and (
            isinstance(time, bool) or not isinstance(time, Real)
        ):
            raise TypeError(
                f"{type(self).__name__}.{self.time_field} must be a number of "
                f"seconds or a timedelta; got {type(time).__name__!r}"
            )
        if isinstance(time, timedelta):
            time = time.total_seconds()
        if time < 0:
            raise ValueError(
                f"{type(self).__name__}.{self.time_field} must be non-negative"
            )
        setattr(self, self.time_field, float(time))

    def __init_subclass__(cls, abstract: bool = False, **kwargs):
        if not abstract:
            cls._declare_time_field()
        super().__init_subclass__(abstract=abstract, **kwargs)

    @classmethod
    def _declare_time_field(cls):
        declared = {
            name
            for parent in cls.__mro__
            for name in parent.__dict__.get("__annotations__", {})
        }
        if cls.time_field in declared:
            return
        if "__annotations__" not in cls.__dict__:
            cls.__annotations__ = {}
        cls.__annotations__[cls.time_field] = float | int | timedelta
