# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 ctkolb
"""Flatten a nested metadata record into scalar columns and tables."""

from __future__ import annotations

import json
from typing import Any

from .extract import EXIF_FIELDS, FLAG_FIELDS

SCALAR_COLUMNS: tuple[str, ...] = (
    "uuid",
    "filename",
    "original_filename",
    "media_type",
    "original_filesize",
    "date",
    "date_original",
    "date_added",
    "date_modified",
    "tzname",
    "tzoffset",
    "width",
    "height",
    "original_width",
    "original_height",
    "orientation",
    "latitude",
    "longitude",
    "place_name",
    "place_address",
    "place_country_code",
    "place_names",
    *EXIF_FIELDS,
    *FLAG_FIELDS,
    "keywords",
    "title",
    "description",
    "rating",
    "score",
    "albums",
    "album_folders",
    "persons",
    "detected_text",
    "ai_caption",
    "ai_caption_confidence",
    "labels",
    "search_labels",
    "search_info",
)


def _join(value: Any, sep: str = ", ") -> str | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return sep.join(str(item) for item in value)
    return str(value)


def _dump(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, default=str, ensure_ascii=False)


def flatten(record: dict[str, Any]) -> dict[str, Any]:
    """Return a flat dict keyed by SCALAR_COLUMNS for CSV/SQLite use."""
    place = record.get("place") or {}
    exif = record.get("exif") or {}
    flags = record.get("flags") or {}
    ml = record.get("ml") or {}

    row: dict[str, Any] = {
        "uuid": record.get("uuid"),
        "filename": record.get("filename"),
        "original_filename": record.get("original_filename"),
        "media_type": record.get("media_type"),
        "original_filesize": record.get("original_filesize"),
        "date": record.get("date"),
        "date_original": record.get("date_original"),
        "date_added": record.get("date_added"),
        "date_modified": record.get("date_modified"),
        "tzname": record.get("tzname"),
        "tzoffset": record.get("tzoffset"),
        "width": record.get("width"),
        "height": record.get("height"),
        "original_width": record.get("original_width"),
        "original_height": record.get("original_height"),
        "orientation": record.get("orientation"),
        "latitude": record.get("latitude"),
        "longitude": record.get("longitude"),
        "place_name": place.get("name"),
        "place_address": place.get("address"),
        "place_country_code": place.get("country_code"),
        "place_names": _dump(place.get("names")),
        "keywords": _join(record.get("keywords")),
        "title": record.get("title"),
        "description": record.get("description"),
        "rating": record.get("rating"),
        "score": record.get("score"),
        "albums": _join(record.get("albums")),
        "album_folders": _join(record.get("album_folders")),
        "persons": _join(record.get("persons")),
        "detected_text": _join(record.get("detected_text")),
        "ai_caption": ml.get("ai_caption"),
        "ai_caption_confidence": ml.get("ai_caption_confidence"),
        "labels": _join(ml.get("labels")),
        "search_labels": _join(ml.get("search_labels")),
        "search_info": _dump(ml.get("search_info")),
    }
    for field in EXIF_FIELDS:
        row[field] = exif.get(field)
    for field in FLAG_FIELDS:
        row[field] = flags.get(field)
    return row


def relations(record: dict[str, Any]) -> dict[str, list[str]]:
    """Return many-to-many values used for the SQLite side tables."""
    ml = record.get("ml") or {}
    return {
        "albums": [a for a in record.get("albums") or [] if a],
        "persons": [p for p in record.get("persons") or [] if p and p != "_UNKNOWN_"],
        "keywords": [k for k in record.get("keywords") or [] if k],
        "labels": [label for label in ml.get("search_labels") or [] if label],
    }
