import pandas as pd
from dataclasses import dataclass, fields as dataclass_fields
from typing import Any, get_type_hints
from base.output.record import Record


@dataclass
class Dataset:
    record: type[Record]
    data: dict[str, list[Any]]

    def __post_init__(self):
        self._field_types = get_type_hints(self.record)
        self._validate_columns()
        self._validate_lengths()

    @classmethod
    def from_dataframe(cls, record: type[Record], df: pd.DataFrame) -> "Dataset":
        type_hints = get_type_hints(record)
        field_names = [field.name for field in dataclass_fields(record)]

        missing_col = [col for col in field_names if col not in df.columns]
        if missing_col:
            raise ValueError(
                f"{record.__name__}: dataframe is missing columns {missing_col}. "
                f"Has: {list(df.columns)}"
            )

        column_data: dict[str, list[Any]] = {}
        for col in field_names:
            column_data[col] = _coerce_column(df[col], type_hints[col]).tolist()
        return cls(record=record, data=column_data)

    @classmethod
    def from_rows(cls, record: type[Record], rows: list[dict]) -> "Dataset":
        field_names = [field.name for field in dataclass_fields(record)]
        return cls.from_dataframe(record, pd.DataFrame(rows, columns=field_names))

    @classmethod
    def from_csv(cls, record: type[Record], path: str, **read_kwargs) -> "Dataset":
        return cls.from_dataframe(record, pd.read_csv(path, **read_kwargs))

    def _validate_columns(self):
        field_names = {field.name for field in dataclass_fields(self.record)}
        extra_col = set(self.data) - field_names
        if extra_col:
            raise ValueError(
                f"{self.record.__name__}: columns {sorted(extra_col)} are not "
                f"fields of the record. Declared: {sorted(field_names)}"
            )
        for key in self.record.primary_key:
            if key not in self.data:
                raise ValueError(
                    f"{self.record.__name__}: primary key column '{key}' missing."
                )

    def _validate_lengths(self):
        lengths = {len(values) for values in self.data.values()}
        if len(lengths) > 1:
            raise ValueError(
                f"{self.record.__name__}: ragged columns, lengths={lengths}"
            )


def _coerce_column(series: pd.Series, field_type: type) -> pd.Series:
    """Best-effort cast a column to its declared type. Geometry/other objects
    pass through untouched."""
    pandas_dtype = {int: "Int64", float: "float64", bool: "boolean", str: "string"}.get(
        field_type
    )
    if pandas_dtype is None:
        return series  # geometry, tuples, anything non-scalar
    return series.astype(pandas_dtype)
