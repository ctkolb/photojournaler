# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 ctkolb
"""Streaming writers for JSON, CSV, and SQLite output."""

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from typing import Any

from .schema import SCALAR_COLUMNS, flatten, relations

_TYPE_OVERRIDES = {
    "original_filesize": "INTEGER",
    "tzoffset": "INTEGER",
    "width": "INTEGER",
    "height": "INTEGER",
    "original_width": "INTEGER",
    "original_height": "INTEGER",
    "orientation": "INTEGER",
    "rating": "INTEGER",
}


class JsonWriter:
    def __init__(self, path: Path) -> None:
        self._fh = path.open("w", encoding="utf-8")
        self._fh.write("[\n")
        self._first = True

    def write(self, record: dict[str, Any]) -> None:
        if not self._first:
            self._fh.write(",\n")
        self._first = False
        json.dump(record, self._fh, default=str, ensure_ascii=False)

    def close(self) -> None:
        self._fh.write("\n]\n")
        self._fh.close()


class CsvWriter:
    def __init__(self, path: Path) -> None:
        self._fh = path.open("w", encoding="utf-8", newline="")
        self._writer = csv.DictWriter(
            self._fh, fieldnames=list(SCALAR_COLUMNS), extrasaction="ignore"
        )
        self._writer.writeheader()

    def write(self, record: dict[str, Any]) -> None:
        self._writer.writerow(flatten(record))

    def close(self) -> None:
        self._fh.close()


class SqliteWriter:
    def __init__(self, path: Path) -> None:
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self._terms: dict[str, dict[str, int]] = {
            "album": {},
            "person": {},
            "keyword": {},
            "label": {},
        }
        self._links: dict[str, list[tuple[str, int]]] = {
            "album": [],
            "person": [],
            "keyword": [],
            "label": [],
        }
        self._create_schema()
        self._photo_cols = list(SCALAR_COLUMNS)
        self._insert = (
            f"INSERT OR REPLACE INTO photos ({', '.join(self._photo_cols)}) "
            f"VALUES ({', '.join('?' * len(self._photo_cols))})"
        )
        self._pending: list[dict[str, Any]] = []
        self._batch = 2000

    def _create_schema(self) -> None:
        cols = []
        for column in SCALAR_COLUMNS:
            if column in ("keywords", "albums", "persons", "labels",
                          "search_labels", "detected_text", "search_info", "place_names"):
                col_type = "TEXT"
            elif column in _TYPE_OVERRIDES:
                col_type = _TYPE_OVERRIDES[column]
            elif column in ("ai_caption_confidence", "latitude", "longitude",
                            "score", "focal_length", "aperture", "shutter_speed",
                            "iso", "exposure_bias", "fps", "duration", "bit_rate",
                            "sample_rate"):
                col_type = "REAL"
            else:
                col_type = "TEXT"
            cols.append(f'"{column}" {col_type}')
        self.conn.execute(f"CREATE TABLE IF NOT EXISTS photos ({', '.join(cols)}, PRIMARY KEY (uuid))")
        for name in self._terms:
            table = f"{name}s"
            self.conn.execute(
                f"CREATE TABLE IF NOT EXISTS {table} ("
                f"id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL)"
            )
            self.conn.execute(
                f"CREATE TABLE IF NOT EXISTS photo_{table} ("
                f"photo_uuid TEXT NOT NULL, {name}_id INTEGER NOT NULL, "
                f"PRIMARY KEY (photo_uuid, {name}_id))"
            )
        self.conn.commit()

    def _term_id(self, kind: str, name: str) -> int:
        cache = self._terms[kind]
        tid = cache.get(name)
        if tid is None:
            cur = self.conn.execute(
                f"INSERT OR IGNORE INTO {kind}s (name) VALUES (?)", (name,)
            )
            if cur.lastrowid:
                tid = cur.lastrowid
            else:
                tid = self.conn.execute(
                    f"SELECT id FROM {kind}s WHERE name = ?", (name,)
                ).fetchone()[0]
            cache[name] = tid
        return tid

    def write(self, record: dict[str, Any]) -> None:
        row = flatten(record)
        self._pending.append(row)
        uuid = record["uuid"]
        for kind_plural, values in relations(record).items():
            kind = kind_plural[:-1]
            for value in values:
                self._links[kind].append((uuid, self._term_id(kind, value)))
        if len(self._pending) >= self._batch:
            self.flush()

    def flush(self) -> None:
        if self._pending:
            self.conn.executemany(
                self._insert,
                [tuple(row.get(col) for col in self._photo_cols) for row in self._pending],
            )
            self._pending.clear()
        for kind, links in self._links.items():
            if links:
                table = f"photo_{kind}s"
                self.conn.executemany(
                    f"INSERT OR IGNORE INTO {table} (photo_uuid, {kind}_id) VALUES (?, ?)",
                    links,
                )
                links.clear()
        self.conn.commit()

    def close(self) -> None:
        self.flush()
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_photos_date ON photos(date)")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_photos_place ON photos(place_country_code)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_photos_camera ON photos(camera_model)"
        )
        self.conn.commit()
        self.conn.close()
