"""Capability-led adapters for normalized Claude Ads inputs."""

from .base import (
    Adapter,
    AdapterCapabilities,
    AdapterError,
    BaseAdapter,
    Capability,
    MutationDisabledError,
)
from .csv_export import CSVExportError, GenericCSVExportAdapter

__all__ = [
    "Adapter",
    "AdapterCapabilities",
    "AdapterError",
    "BaseAdapter",
    "CSVExportError",
    "Capability",
    "GenericCSVExportAdapter",
    "MutationDisabledError",
]
