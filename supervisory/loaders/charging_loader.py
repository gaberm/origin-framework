from typing import Any
import psycopg2 as psycopg
from models.inputs import ChargingInputs, VehicleToAdd, VehicleToRemove
from supervisory.loaders.base_loader import BaseLoader, BaseInputLoader
from supervisory.time import TimeRange


class VehicleToAddLoader(BaseLoader[VehicleToAdd]):
    @staticmethod
    def _extract(
        conn: psycopg.extensions.connection, time_interval: TimeRange
    ) -> list[tuple[Any, ...]]:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT vehicle_id, soc_at_arrival, arrival_time
                FROM arrived_vehicles
                WHERE arrival_time >= %s AND arrival_time < %s
                """,
                (time_interval.start_time, time_interval.end_time),
            )
            rows = cursor.fetchall()
        return rows

    @staticmethod
    def _transform(
        rows: list[tuple[Any, ...]], time_interval: TimeRange, charge_rate: float
    ) -> list[tuple[Any, ...]]:
        return [
            (
                row[0],
                max(
                    0.0,
                    row[1]
                    - (time_interval.end_time - time_interval.start_time) * charge_rate,
                ),
            )
            for row in rows
        ]

    @staticmethod
    def _build(rows: list[tuple[Any, ...]]) -> tuple[VehicleToAdd, ...]:
        return tuple(
            VehicleToAdd(
                vehicle_id=row[0],
                initial_soc=row[1],
            )
            for row in rows
        )

    @staticmethod
    def load_input(
        conn: psycopg.extensions.connection,
        time_interval: TimeRange,
        charge_rate: float,
    ) -> tuple[VehicleToAdd, ...]:
        rows = VehicleToAddLoader._extract(conn, time_interval)
        rows = VehicleToAddLoader._transform(rows, time_interval, charge_rate)
        return VehicleToAddLoader._build(rows)


class VehicleToRemoveLoader(BaseLoader[VehicleToRemove]):
    @staticmethod
    def _extract(
        conn: psycopg.extensions.connection, time_interval: TimeRange
    ) -> list[tuple[Any, ...]]:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT vehicle_id
                FROM departed_vehicles
                WHERE departure_time >= %s AND departure_time < %s
                """,
                (time_interval.start_time, time_interval.end_time),
            )
            rows = cursor.fetchall()
        return rows

    @staticmethod
    def _build(rows: list[tuple[Any, ...]]) -> tuple[VehicleToRemove, ...]:
        return tuple(
            VehicleToRemove(
                vehicle_id=row[0],
            )
            for row in rows
        )

    @staticmethod
    def load_input(
        conn: psycopg.extensions.connection, time_interval: TimeRange
    ) -> tuple[VehicleToRemove, ...]:
        rows = VehicleToRemoveLoader._extract(conn, time_interval)
        return VehicleToRemoveLoader._build(rows)


class ChargingInputLoader(BaseInputLoader):
    def __init__(self, charge_rate: float):
        self.charge_rate = charge_rate

    @classmethod
    def from_config(cls, config):
        return cls(charge_rate=config.models.charging.charging_rate)

    def load_input(
        self, conn: psycopg.extensions.connection, time_interval: TimeRange
    ) -> ChargingInputs:
        return ChargingInputs(
            vehicles_to_add=tuple(
                VehicleToAddLoader.load_input(conn, time_interval, self.charge_rate)
            ),
            vehicles_to_remove=tuple(
                VehicleToRemoveLoader.load_input(conn, time_interval)
            ),
        )
