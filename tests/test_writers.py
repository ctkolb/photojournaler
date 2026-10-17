"""Tests for the JSON, CSV, and SQLite writers."""

from __future__ import annotations

import csv
import json
import sqlite3

from photosmeta import build_record
from photosmeta.schema import SCALAR_COLUMNS
from photosmeta.writers import CsvWriter, JsonWriter, SqliteWriter

from conftest import FakeSearchInfo, make_photo


def _write_all(records, outdir, prefix="p"):
    paths = {}
    for name, writer_cls in (("json", JsonWriter), ("csv", CsvWriter), ("sqlite", SqliteWriter)):
        path = outdir / f"{prefix}.{name}"
        writer = writer_cls(path)
        for record in records:
            writer.write(record)
        writer.close()
        paths[name] = path
    return paths


def test_json_writer_roundtrip(records, tmp_path):
    path = tmp_path / "photos.json"
    writer = JsonWriter(path)
    for record in records:
        writer.write(record)
    writer.close()
    loaded = json.loads(path.read_text())
    assert loaded == json.loads(json.dumps(records, default=str))
    assert len(loaded) == len(records)


def test_json_writer_empty(tmp_path):
    path = tmp_path / "empty.json"
    JsonWriter(path).close()
    assert json.loads(path.read_text()) == []


def test_csv_header_matches_columns(records, tmp_path):
    paths = _write_all(records, tmp_path)
    with paths["csv"].open() as fh:
        reader = csv.reader(fh)
        header = next(reader)
    assert header == list(SCALAR_COLUMNS)


def test_csv_row_count_and_values(records, tmp_path):
    paths = _write_all(records, tmp_path)
    with paths["csv"].open() as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == len(records)
    assert rows[0]["uuid"] == records[0]["uuid"]
    assert rows[0]["camera_model"] == "iPhone 14 Pro"


def test_csv_quotes_embedded_commas(tmp_path):
    photo = make_photo(title="Hello, World", description='say "hi", ok')
    path = tmp_path / "p.csv"
    writer = CsvWriter(path)
    writer.write(build_record(photo))
    writer.close()
    with path.open() as fh:
        row = next(csv.DictReader(fh))
    assert row["title"] == "Hello, World"
    assert row["description"] == 'say "hi", ok'


def test_sqlite_tables_created(records, tmp_path):
    paths = _write_all(records, tmp_path)
    con = sqlite3.connect(paths["sqlite"])
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in ("photos", "albums", "persons", "keywords", "labels",
                  "photo_albums", "photo_persons", "photo_keywords", "photo_labels"):
        assert table in tables


def test_sqlite_photo_count(records, tmp_path):
    paths = _write_all(records, tmp_path)
    con = sqlite3.connect(paths["sqlite"])
    assert con.execute("SELECT COUNT(*) FROM photos").fetchone()[0] == len(records)


def test_sqlite_relations_populated(records, tmp_path):
    paths = _write_all(records, tmp_path)
    con = sqlite3.connect(paths["sqlite"])
    albums = {r[0] for r in con.execute("SELECT name FROM albums")}
    persons = {r[0] for r in con.execute("SELECT name FROM persons")}
    labels = {r[0] for r in con.execute("SELECT name FROM labels")}
    assert albums == {"Vacation", "Favorites"}
    assert persons == {"Alice", "Bob"}
    assert labels == {"Beach", "People", "Outdoor"}


def test_sqlite_unknown_person_excluded(records, tmp_path):
    paths = _write_all(records, tmp_path)
    con = sqlite3.connect(paths["sqlite"])
    assert con.execute(
        "SELECT COUNT(*) FROM persons WHERE name = '_UNKNOWN_'"
    ).fetchone()[0] == 0


def test_sqlite_join_row_counts(records, tmp_path):
    paths = _write_all(records, tmp_path)
    con = sqlite3.connect(paths["sqlite"])
    assert con.execute("SELECT COUNT(*) FROM photo_albums").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM photo_persons").fetchone()[0] == 2


def test_sqlite_column_types(records, tmp_path):
    paths = _write_all(records, tmp_path)
    con = sqlite3.connect(paths["sqlite"])
    info = {row[1]: row[2] for row in con.execute("PRAGMA table_info(photos)")}
    assert info["latitude"] == "REAL"
    assert info["favorite"] == "TEXT"
    assert info["tzoffset"] == "INTEGER"


def test_sqlite_boolean_stored_as_boolish(records, tmp_path):
    paths = _write_all(records, tmp_path)
    con = sqlite3.connect(paths["sqlite"])
    value = con.execute(
        "SELECT favorite FROM photos WHERE uuid = ?", (records[0]["uuid"],)
    ).fetchone()[0]
    assert value in (1, True, "1")


def test_sqlite_rewrite_is_idempotent(records, tmp_path):
    path = tmp_path / "p.sqlite"
    for _ in range(2):
        writer = SqliteWriter(path)
        for record in records:
            writer.write(record)
        writer.close()
    con = sqlite3.connect(path)
    assert con.execute("SELECT COUNT(*) FROM photos").fetchone()[0] == len(records)
    assert con.execute("SELECT COUNT(*) FROM albums").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM photo_albums").fetchone()[0] == 2


def test_sqlite_batching_across_many_records(tmp_path):
    photos = [make_photo(uuid=f"uuid-{i}") for i in range(10)]
    records = [build_record(p) for p in photos]
    path = tmp_path / "p.sqlite"
    writer = SqliteWriter(path)
    writer._batch = 3
    for record in records:
        writer.write(record)
    writer.close()
    con = sqlite3.connect(path)
    assert con.execute("SELECT COUNT(*) FROM photos").fetchone()[0] == 10


def test_sqlite_labels_use_search_labels(tmp_path):
    photo = make_photo(
        labels_normalized=["people"],
        search_info=FakeSearchInfo(labels=["People"]),
    )
    path = tmp_path / "p.sqlite"
    writer = SqliteWriter(path)
    writer.write(build_record(photo))
    writer.close()
    con = sqlite3.connect(path)
    names = {r[0] for r in con.execute("SELECT name FROM labels")}
    assert names == {"People"}
