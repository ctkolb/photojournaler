"""Gather metadata from a local macOS Photos library (iCloud Photos)."""

from .extract import build_record
from .schema import SCALAR_COLUMNS, flatten, relations
from .writers import CsvWriter, JsonWriter, SqliteWriter

__all__ = [
    "build_record",
    "flatten",
    "relations",
    "SCALAR_COLUMNS",
    "JsonWriter",
    "CsvWriter",
    "SqliteWriter",
]

__version__ = "0.1.0"
