import dataclasses
import h3
from records import ModelOutput, ShapeType


def assign_cells(output: ModelOutput, resolution: int) -> ModelOutput:
    indexed = ModelOutput()
    for record in output.all_records():
        shape_type = getattr(record, "shape_type", None)
        shape_coord = getattr(record, "shape_coord", None)
        if shape_type and shape_coord:
            record = dataclasses.replace(
                record, cell_ids=_cells_for_shape(shape_type, shape_coord, resolution)
            )
        indexed.add(record)
    return indexed


def _cells_for_shape(
    shape_type: str, shape_coord: list[tuple[float, float]], resolution: int
) -> list[str]:
    if shape_type == ShapeType.POINT:
        lat, lon = shape_coord[0]
        return [h3.geo_to_h3(lat, lon, resolution)]

    if shape_type == ShapeType.LINESTRING:
        cells = []
        for i in range(len(shape_coord) - 1):
            lat1, lon1 = shape_coord[i]
            lat2, lon2 = shape_coord[i + 1]
            start = h3.geo_to_h3(lat1, lon1, resolution)
            end = h3.geo_to_h3(lat2, lon2, resolution)
            cells.extend(h3.h3_line(start, end))
        return list(dict.fromkeys(cells))

    if shape_type == ShapeType.POLYGON:
        geo = {
            "type": "Polygon",
            "coordinates": [[[lon, lat] for lat, lon in shape_coord]],
        }
        return list(h3.polyfill_geojson(geo, resolution))

    raise ValueError(f"Unknown shape type: {shape_type}")
