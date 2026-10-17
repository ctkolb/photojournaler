"""Tests for analyze_photos_metadata (loading, stats, and HTML rendering)."""

from __future__ import annotations

import datetime as dt
from collections import Counter

import analyze_photos_metadata as az
from photosmeta.writers import JsonWriter, SqliteWriter

from conftest import make_photo


def _write(records, outdir):
    json_path = outdir / "p.json"
    sqlite_path = outdir / "p.sqlite"
    jw, sw = JsonWriter(json_path), SqliteWriter(sqlite_path)
    for record in records:
        jw.write(record)
        sw.write(record)
    jw.close()
    sw.close()
    return json_path, sqlite_path


def _comparable(ds):
    return {
        "total": ds.total,
        "photos": ds.photos,
        "videos": ds.videos,
        "with_gps": ds.with_gps,
        "with_people": ds.with_people,
        "favorites": ds.favorites,
        "years": dict(ds.years),
        "hours": dict(ds.hours),
        "weekdays": dict(ds.weekdays),
        "cameras": dict(ds.cameras),
        "countries": dict(ds.countries),
        "persons": dict(ds.persons),
        "labels": dict(ds.labels),
        "albums": dict(ds.albums),
        "earliest": ds.earliest,
        "latest": ds.latest,
    }


def test_json_and_sqlite_loaders_agree(records, tmp_path):
    json_path, sqlite_path = _write(records, tmp_path)
    assert _comparable(az.load_json(json_path)) == _comparable(az.load_sqlite(sqlite_path))


def test_loader_counts(records, tmp_path):
    json_path, sqlite_path = _write(records, tmp_path)
    for ds in (az.load_json(json_path), az.load_sqlite(sqlite_path)):
        assert ds.total == 3
        assert ds.photos == 2
        assert ds.videos == 1
        assert ds.persons == Counter({"Alice": 1, "Bob": 1})
        assert ds.countries == Counter({"US": 1, "FR": 1})
        assert ds.albums == Counter({"Vacation": 1, "Favorites": 1})
        assert set(ds.labels) == {"Beach", "People", "Outdoor"}


def test_local_hour_uses_capture_offset(records, tmp_path):
    """23:30 at UTC-06:00 must bucket to hour 23, not the UTC hour 05."""

    json_path, sqlite_path = _write(records, tmp_path)
    for ds in (az.load_json(json_path), az.load_sqlite(sqlite_path)):
        assert ds.hours[23] == 1
        assert ds.hours.get(5, 0) == 0
        assert 2024 in ds.years


def test_unsupported_input_raises(tmp_path):
    path = tmp_path / "x.txt"
    path.write_text("hi")
    try:
        az.load(path)
    except SystemExit:
        pass
    else:
        raise AssertionError("expected SystemExit")


def test_chrono_sorts_numerically():
    assert az._chrono(Counter({2020: 5, 2019: 1})) == [("2019", 1), ("2020", 5)]


def test_ranked_sorts_by_count():
    assert az._ranked(Counter({"a": 1, "b": 9}), 10) == [("b", 9), ("a", 1)]


def test_ranked_respects_limit():
    assert len(az._ranked(Counter({str(i): i for i in range(50)}), 5)) == 5


def test_weekday_and_hour_rows():
    weekdays = az._weekday_rows(Counter({0: 3}))
    assert [label for label, _ in weekdays] == az.WEEKDAYS
    assert weekdays[0] == ("Mon", 3)
    hours = az._hour_rows(Counter({13: 2}))
    assert len(hours) == 24
    assert hours[13] == ("13:00", 2)


def test_is_leap():
    assert az._is_leap(2024)
    assert az._is_leap(2000)
    assert not az._is_leap(1900)
    assert not az._is_leap(2023)


def test_heatmap_covers_gap_years():
    ds = az.Dataset(source="t")
    az._add_time(ds, dt.datetime(2020, 5, 1, 12))
    az._add_time(ds, dt.datetime(2022, 5, 1, 12))
    html = az._heatmap(ds)
    assert "2020" in html and "2021" in html and "2022" in html


def test_heatmap_leap_year_does_not_clip():
    # 2012-01-01 is a Sunday (weekday 6) and 2012 is a leap year -> 54 columns.
    import re

    ds = az.Dataset(source="t")
    az._add_time(ds, dt.datetime(2012, 1, 1, 12))
    html = az._heatmap(ds)
    widths = [int(w) for w in re.findall(r'width="(\d+)"', html)]
    assert max(widths) >= 700


def test_heatmap_counts_rects():
    ds = az.Dataset(source="t")
    az._add_time(ds, dt.datetime(2021, 3, 8, 12))
    html = az._heatmap(ds)
    assert html.count("<rect ") == 365
    assert "2021-03-08: 1 assets" in html


def test_heatmap_without_dates():
    assert az._heatmap(az.Dataset(source="t")) == "<p>No date data.</p>"


def test_bar_chart_renders_rows():
    html = az._bar_chart([("a", 3), ("b", 6)])
    assert html.count("bar-row") == 2
    assert "b" in html and "6" in html


def test_bar_chart_empty():
    assert az._bar_chart([]) == "<p>No data.</p>"


def test_write_html_contains_expected_sections(records, tmp_path):
    json_path, _ = _write(records, tmp_path)
    ds = az.load_json(json_path)
    out = tmp_path / "report.html"
    az.write_html(ds, out, top=10)
    html = out.read_text()
    assert html.startswith("<!DOCTYPE html>")
    assert html.rstrip().endswith("</html>")
    assert "Top cameras" in html
    assert "Calendar heatmap" in html
    assert "card-value" in html


def test_print_report_smoke(records, tmp_path, capsys):
    json_path, _ = _write(records, tmp_path)
    ds = az.load_json(json_path)
    az.print_report(ds, top=5)
    captured = capsys.readouterr().out
    assert "Assets" in captured
    assert "Top people" in captured


def test_main_missing_input_returns_error(tmp_path, capsys):
    code = az.main([str(tmp_path / "nope.sqlite")])
    assert code == 1
    assert "not found" in capsys.readouterr().err


def test_main_writes_html(records, tmp_path):
    _, sqlite_path = _write(records, tmp_path)
    out = tmp_path / "r.html"
    code = az.main([str(sqlite_path), "--html", str(out), "--top", "3"])
    assert code == 0
    assert out.exists() and out.read_text().rstrip().endswith("</html>")


def test_main_reads_json(tmp_path, records):
    json_path, _ = _write(records, tmp_path)
    out = tmp_path / "r.html"
    assert az.main([str(json_path), "--html", str(out)]) == 0
    assert out.exists()


def test_empty_dataset_report_and_html(tmp_path, capsys):
    ds = az.Dataset(source="empty")
    az.print_report(ds, top=5)
    assert "n/a" in capsys.readouterr().out
    out = tmp_path / "empty.html"
    az.write_html(ds, out, top=5)
    html = out.read_text()
    assert html.rstrip().endswith("</html>")
    assert "No date data." in html
    assert "No data." in html


def test_empty_sqlite_loads(tmp_path):
    from photosmeta.writers import SqliteWriter

    path = tmp_path / "empty.sqlite"
    SqliteWriter(path).close()
    ds = az.load_sqlite(path)
    assert ds.total == 0
    assert ds.earliest is None
