from records.base_record import ShapeType


def validate_shape(
    shape_coord: list[tuple[float, float]], shape_type: ShapeType
) -> None:
    if not shape_coord:
        raise ValueError("Shape must have at least one coordinate")
    if shape_type == ShapeType.POINT and len(shape_coord) != 1:
        raise ValueError("Point shape must have exactly one coordinate")
    if shape_type in (ShapeType.LINESTRING, ShapeType.POLYGON) and len(shape_coord) < 2:
        raise ValueError(f"{shape_type} must have at least 2 coordinates")

    for i, coord in enumerate(shape_coord):
        if not isinstance(coord, tuple) or len(coord) != 2:
            raise ValueError(f"Coordinate {i} must be a 2-tuple")
        if not all(isinstance(val, (int, float)) for val in coord):
            raise ValueError(f"Coordinate {i} values must be numbers")
        if abs(coord[0]) > 90 or abs(coord[1]) > 180:
            raise ValueError(f"Coordinate {i} values must be valid lat/lon")
