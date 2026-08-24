from abc import ABC, abstractmethod
from typing import Callable

from app.schemas import CriteriaConfig, JobListing

ProgressCallback = Callable[[str], None]


class Connector(ABC):
    """A pluggable job source. Each connector knows how to turn the user's
    criteria into a list of raw JobListing objects; filtering/scoring happens
    downstream in the pipeline, not inside the connector."""

    name: str

    @abstractmethod
    def search(self, criteria: CriteriaConfig, on_progress: ProgressCallback | None = None) -> list[JobListing]:
        ...
