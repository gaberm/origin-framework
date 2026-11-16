from abc import ABC, abstractmethod


class Transform(ABC):

    @abstractmethod
    def transform(self, rows: list[dict]) -> list[dict]: ...
