# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 ctkolb
"""Tests for photosmeta.schema.flatten and relations."""

from __future__ import annotations

import json

from photosmeta.extract import EXIF_FIELDS, FLAG_FIELDS
from photosmeta.schema import SCALAR_COLUMNS, flatten, relations


def test_flatten_returns_exactly_scalar_columns(record):
    row = flatten(record)
    assert set(row) == set(SCALAR_COLUMNS)
    assert len(SCALAR_COLUMNS) == len(set(SCALAR_COLUMNS))


def test_lists_are_joined_with_commas(record):
    row = flatten(record)
    assert row["keywords"] == "summer, beach"
    assert row["albums"] == "Vacation, Favorites"
    assert row["persons"] == "Alice, _UNKNOWN_, Bob"


def test_none_lists_become_none():
    row = flatten({
        "uuid": "x",
        "keywords": None,
        "albums": None,
        "album_folders": None,
        "persons": None,
        "detected_text": None,
    })
    assert row["keywords"] is None
    assert row["persons"] is None


def test_empty_lists_become_empty_string():
    row = flatten({"uuid": "x", "keywords": [], "albums": []})
    assert row["keywords"] == ""
    assert row["albums"] == ""


def test_exif_and_flags_are_promoted_to_top_level(record):
    row = flatten(record)
    for field in EXIF_FIELDS:
        assert field in row
    for field in FLAG_FIELDS:
        assert field in row
    assert row["camera_model"] == "iPhone 14 Pro"
    assert row["favorite"] is True


def test_place_columns(record):
    row = flatten(record)
    assert row["place_name"] == "Beach, FL"
    assert row["place_country_code"] == "US"
    assert row["place_names"] is not None


def test_complex_fields_are_json_encoded(record):
    row = flatten(record)
    assert json.loads(row["search_info"])["country"] == "United States"
    assert json.loads(row["place_names"]) == [[]]


def test_ml_derived_columns(record):
    row = flatten(record)
    assert row["ai_caption"] == "a sandy beach"
    assert row["ai_caption_confidence"] == 0.9
    assert row["labels"] == "people, beach"
    assert row["search_labels"] == "Beach, People"


def test_flatten_without_ml_section():
    row = flatten({"uuid": "x", "exif": {}, "flags": {}, "place": None})
    assert row["ai_caption"] is None
    assert row["labels"] is None
    assert row["search_info"] is None


def test_relations_filters_unknown_and_blanks():
    result = relations({
        "albums": ["A", "", None, "B"],
        "persons": ["Alice", "_UNKNOWN_", "", "Bob"],
        "keywords": ["x", "", None],
        "ml": {"search_labels": ["Beach", "", "People"]},
    })
    assert result["albums"] == ["A", "B"]
    assert result["persons"] == ["Alice", "Bob"]
    assert result["keywords"] == ["x"]
    assert result["labels"] == ["Beach", "People"]


def test_relations_label_source_is_search_labels_not_normalized():
    result = relations({
        "ml": {"labels": ["people"], "search_labels": ["People"]},
    })
    assert result["labels"] == ["People"]


def test_relations_handles_missing_ml():
    result = relations({"albums": ["A"]})
    assert result["labels"] == []
    assert result["persons"] == []
