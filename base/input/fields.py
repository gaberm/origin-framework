from __future__ import annotations
from dataclasses import fields as dataclass_fields


class Fields:
    """The fields an Input returns. Can be used in two ways:

    * Explicit: Fields((EV, "veh_id", "soc")) selects EV.veh_id and EV.soc.
      Pass one tuple per record to select from several, e.g.
      Fields((EV, "veh_id"), (Battery, "capacity")).
    * Implicit: Fields("veh_id", "soc") selects from the Input's from_ record.

    Fields of a joined record can only be selected explicitly.
    """

    __slots__ = ("selected_fields",)

    def __init__(self, *args):
        if not args:
            raise TypeError("Fields(...) requires at least one field name")
        self.selected_fields = {}
        if self._is_explicit(args):
            for arg in args:
                self.selected_fields.update(self._parse_field(arg))
        else:
            field_names = (
                args[0]
                if len(args) == 1 and isinstance(args[0], (list, tuple))
                else args
            )
            if not field_names:
                raise TypeError("Fields(...) requires at least one field name")
            self._validate_fields(field_names)
            self.selected_fields = {None: tuple(field_names)}

    @staticmethod
    def _is_explicit(args) -> bool:
        return (
            isinstance(args[0], tuple)
            and bool(args[0])
            and isinstance(args[0][0], type)
        )

    def _parse_field(self, arg) -> tuple:
        if not (isinstance(arg, tuple) and arg and isinstance(arg[0], type)):
            raise TypeError(f"Fields: expected (Entity, field, ...) tuple, got {arg!r}")
        entity, *field_names = arg
        if not field_names:
            raise TypeError(f"Fields: no field names given for {entity.__name__}")
        valid_fields = {
            dataclass_field.name for dataclass_field in dataclass_fields(entity)
        }
        self._validate_fields(field_names, valid_fields)
        return {entity.table_name: tuple(field_names)}

    @staticmethod
    def _validate_fields(field_names, valid_fields: set[str] = None) -> None:
        for field_name in field_names:
            if not isinstance(field_name, str):
                raise TypeError(
                    f"Fields: expected string field name, got {field_name!r}"
                )
            if valid_fields is not None and field_name not in valid_fields:
                raise AttributeError(
                    f"Fields: no field {field_name!r}. Valid: {sorted(valid_fields)}"
                )

    def __repr__(self) -> str:
        parts = []
        for table_name, names in self.selected_fields.items():
            if table_name is not None:
                parts.append(table_name)
            parts.append(repr(names))
        return f"Fields({', '.join(parts)})"
