"""Gather metadata from a local macOS Photos library (iCloud Photos)."""

from .extract import build_record
from .journal import (
    Batch,
    Photo,
    build_batches,
    build_journal,
    day_narrative,
    infer_owner_windows,
    photo_url,
    read_photos,
    render,
    render_html,
    select_owner_photos,
)
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
    "Photo",
    "Batch",
    "read_photos",
    "infer_owner_windows",
    "select_owner_photos",
    "build_batches",
    "render",
    "render_html",
    "build_journal",
    "photo_url",
    "day_narrative",
]

__version__ = "0.1.0"
