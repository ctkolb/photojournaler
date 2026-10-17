#!/usr/bin/env python3
"""Analyze an exported Photos metadata set and print / render statistics.

Reads either ``photos.sqlite`` or ``photos.json`` produced by
``export_photos_metadata.py``. Prints a console summary and, with ``--html``,
writes a self-contained HTML report (inline CSS + SVG, no dependencies).

Local wall-clock time is used throughout, computed from each asset's original
capture timestamp with its UTC offset preserved.
"""

from __future__ import annotations

import argparse
import html
import json
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@dataclass
class Dataset:
    source: str = ""
    total: int = 0
    photos: int = 0
    videos: int = 0
    with_gps: int = 0
    with_people: int = 0
    favorites: int = 0
    earliest: datetime | None = None
    latest: datetime | None = None
    years: Counter = field(default_factory=Counter)
    months: Counter = field(default_factory=Counter)
    hours: Counter = field(default_factory=Counter)
    weekdays: Counter = field(default_factory=Counter)
    days: Counter = field(default_factory=Counter)
    cameras: Counter = field(default_factory=Counter)
    countries: Counter = field(default_factory=Counter)
    persons: Counter = field(default_factory=Counter)
    labels: Counter = field(default_factory=Counter)
    albums: Counter = field(default_factory=Counter)


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _add_time(ds: Dataset, when: datetime | None) -> None:
    if when is None:
        return
    ds.years[when.year] += 1
    ds.months[when.month] += 1
    ds.hours[when.hour] += 1
    ds.weekdays[when.weekday()] += 1
    ds.days[when.strftime("%Y-%m-%d")] += 1
    if ds.earliest is None or when < ds.earliest:
        ds.earliest = when
    if ds.latest is None or when > ds.latest:
        ds.latest = when


def load_sqlite(path: Path) -> Dataset:
    ds = Dataset(source=str(path))
    con = sqlite3.connect(path)

    for media_type, count in con.execute(
        "SELECT media_type, COUNT(*) FROM photos GROUP BY 1"
    ):
        ds.total += count
        if media_type == "video":
            ds.videos += count
        else:
            ds.photos += count

    def scalar(query: str) -> int:
        return con.execute(query).fetchone()[0] or 0

    ds.with_gps = scalar(
        "SELECT COUNT(*) FROM photos WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
    )
    ds.favorites = scalar("SELECT COUNT(*) FROM photos WHERE favorite = 1")
    ds.with_people = scalar("SELECT COUNT(DISTINCT photo_uuid) FROM photo_persons")

    for date_original, date in con.execute("SELECT date_original, date FROM photos"):
        _add_time(ds, _parse(date_original) or _parse(date))

    for column, counter in (
        ("camera_model", ds.cameras),
        ("place_country_code", ds.countries),
    ):
        for value, count in con.execute(
            f"SELECT {column}, COUNT(*) FROM photos "
            f"WHERE {column} IS NOT NULL AND {column} != '' GROUP BY 1"
        ):
            counter[value] = count

    for table, counter in (("persons", ds.persons), ("labels", ds.labels),
                           ("albums", ds.albums)):
        for name, count in con.execute(
            f"SELECT t.name, COUNT(*) c FROM {table} t "
            f"JOIN photo_{table} x ON x.{table[:-1]}_id = t.id "
            f"GROUP BY 1"
        ):
            counter[name] = count
    con.close()
    return ds


def load_json(path: Path) -> Dataset:
    ds = Dataset(source=str(path))
    with path.open(encoding="utf-8") as fh:
        records = json.load(fh)

    for record in records:
        ds.total += 1
        if record.get("media_type") == "video":
            ds.videos += 1
        else:
            ds.photos += 1
        if record.get("latitude") is not None and record.get("longitude") is not None:
            ds.with_gps += 1
        flags = record.get("flags") or {}
        if flags.get("favorite"):
            ds.favorites += 1
        persons = [p for p in record.get("persons") or [] if p and p != "_UNKNOWN_"]
        if persons:
            ds.with_people += 1
        for name in persons:
            ds.persons[name] += 1
        for name in record.get("albums") or []:
            ds.albums[name] += 1

        exif = record.get("exif") or {}
        if exif.get("camera_model"):
            ds.cameras[exif["camera_model"]] += 1
        place = record.get("place") or {}
        if place.get("country_code"):
            ds.countries[place["country_code"]] += 1

        ml = record.get("ml") or {}
        labels = ml.get("search_labels") or ml.get("labels") or []
        for label in labels:
            ds.labels[label] += 1

        _add_time(ds, _parse(record.get("date_original")) or _parse(record.get("date")))
    return ds


def load(path: Path) -> Dataset:
    suffix = path.suffix.lower()
    if suffix == ".sqlite" or suffix == ".db":
        return load_sqlite(path)
    if suffix == ".json":
        return load_json(path)
    raise SystemExit(f"Unsupported input: {path} (expected .sqlite or .json)")


def _ranked(counter: Counter, limit: int) -> list[tuple[str, int]]:
    return [(str(key), count) for key, count in counter.most_common(limit)]


def _chrono(counter: Counter) -> list[tuple[str, int]]:
    return [(str(key), count) for key, count in sorted(counter.items())]


def _weekday_rows(counter: Counter) -> list[tuple[str, int]]:
    return [(WEEKDAYS[d], counter.get(d, 0)) for d in range(7)]


def _hour_rows(counter: Counter) -> list[tuple[str, int]]:
    return [(f"{h:02d}:00", counter.get(h, 0)) for h in range(24)]


def print_report(ds: Dataset, top: int) -> None:
    def pct(part: int) -> str:
        return f"{part / ds.total * 100:.1f}%" if ds.total else "n/a"

    print(f"Source            {ds.source}")
    print(f"Assets            {ds.total:,}  ({ds.photos:,} photos, {ds.videos:,} videos)")
    if ds.earliest and ds.latest:
        print(f"Date range        {ds.earliest.date()} -> {ds.latest.date()}")
    print(f"With GPS          {ds.with_gps:,}  ({pct(ds.with_gps)})")
    print(f"With people       {ds.with_people:,}  ({pct(ds.with_people)})")
    print(f"Favorites         {ds.favorites:,}  ({pct(ds.favorites)})")

    def section(title: str, rows: list[tuple[str, int]]) -> None:
        rows = [(key, count) for key, count in rows if count] or rows
        if not rows:
            return
        print(f"\n{title}")
        width = max(len(str(key)) for key, _ in rows)
        for key, count in rows:
            print(f"  {str(key):<{width}}  {count:>8,}")

    section("Assets per year", _chrono(ds.years))
    section("Time of day (local hour)", _hour_rows(ds.hours))
    section("Day of week", _weekday_rows(ds.weekdays))
    section("Top cameras", _ranked(ds.cameras, top))
    section("Top countries", _ranked(ds.countries, top))
    section("Top people", _ranked(ds.persons, top))
    section("Top labels", _ranked(ds.labels, top))
    section("Top albums", _ranked(ds.albums, top))


HEAT_COLORS = ["#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"]


def _bar_chart(rows: list[tuple[str, int]]) -> str:
    if not rows:
        return "<p>No data.</p>"
    peak = max(count for _, count in rows) or 1
    out = ['<div class="bars">']
    for key, count in rows:
        width = max(count / peak * 100, 1)
        out.append(
            '<div class="bar-row">'
            f'<span class="bar-label">{html.escape(str(key))}</span>'
            f'<span class="bar-track"><span class="bar-fill" style="width:{width:.1f}%"></span></span>'
            f'<span class="bar-value">{count:,}</span>'
            "</div>"
        )
    out.append("</div>")
    return "".join(out)


def _heatmap(ds: Dataset) -> str:
    if not ds.days or ds.earliest is None:
        return "<p>No date data.</p>"
    peak = max(ds.days.values()) or 1
    thresholds = [peak * f for f in (0.02, 0.1, 0.3, 0.6)]

    def color(count: int) -> str:
        if count == 0:
            return HEAT_COLORS[0]
        for i, t in enumerate(thresholds):
            if count <= t:
                return HEAT_COLORS[i + 1]
        return HEAT_COLORS[-1]

    cell, gap = 11, 2
    rows = []
    for year in range(ds.latest.year, ds.earliest.year - 1, -1):
        days_in_year = 366 if _is_leap(year) else 365
        start = datetime(year, 1, 1)
        offset = start.weekday()
        span = offset + days_in_year
        cells = []
        for i in range(offset, span):
            col, row = divmod(i, 7)
            date = datetime.fromordinal(start.toordinal() + (i - offset))
            key = date.strftime("%Y-%m-%d")
            count = ds.days.get(key, 0)
            x = col * (cell + gap)
            y = row * (cell + gap)
            title = f"{key}: {count:,} assets"
            cells.append(
                f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" '
                f'rx="2" fill="{color(count)}"><title>{title}</title></rect>'
            )
        width = ((span + 6) // 7) * (cell + gap)
        height = 7 * (cell + gap)
        rows.append(
            f'<div class="heat-year"><span class="heat-year-label">{year}</span>'
            f'<svg width="{width}" height="{height}" role="img">{"".join(cells)}</svg></div>'
        )
    legend = " ".join(
        f'<span class="heat-cell" style="background:{c}"></span>' for c in HEAT_COLORS
    )
    return f'<div class="heat">{"".join(rows)}</div><p class="legend">Less {legend} More</p>'


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _card(label: str, value: str) -> str:
    return f'<div class="card"><div class="card-value">{value}</div><div class="card-label">{label}</div></div>'


def write_html(ds: Dataset, path: Path, top: int) -> None:
    def pct(part: int) -> str:
        return f"{part / ds.total * 100:.1f}%" if ds.total else "n/a"

    def block(title: str, rows: list[tuple[str, int]]) -> str:
        return f'<section><h2>{html.escape(title)}</h2>{_bar_chart(rows)}</section>'

    date_range = (
        f"{ds.earliest.date()} &rarr; {ds.latest.date()}"
        if ds.earliest and ds.latest else "n/a"
    )
    doc = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Photos metadata report</title>
<style>
:root {{ color-scheme: light dark; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  margin: 0; padding: 2rem; background: #f6f8fa; color: #1f2328; }}
h1 {{ font-size: 1.6rem; margin: 0 0 .25rem; }}
.sub {{ color: #656d76; margin-bottom: 1.5rem; }}
.cards {{ display: flex; flex-wrap: wrap; gap: .75rem; margin-bottom: 1.5rem; }}
.card {{ background: #fff; border: 1px solid #d0d7de; border-radius: 10px;
  padding: .9rem 1.1rem; min-width: 120px; }}
.card-value {{ font-size: 1.4rem; font-weight: 600; }}
.card-label {{ font-size: .8rem; color: #656d76; }}
section {{ background: #fff; border: 1px solid #d0d7de; border-radius: 10px;
  padding: 1rem 1.25rem; margin-bottom: 1rem; }}
h2 {{ font-size: 1rem; margin: 0 0 .75rem; }}
.bars {{ display: flex; flex-direction: column; gap: 4px; }}
.bar-row {{ display: flex; align-items: center; gap: .6rem; font-size: .82rem; }}
.bar-label {{ width: 150px; text-align: right; color: #424a53;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.bar-track {{ flex: 1; background: #eaeef2; border-radius: 4px; height: 14px; }}
.bar-fill {{ display: block; height: 100%; background: #1f6feb; border-radius: 4px; }}
.bar-value {{ width: 70px; text-align: right; color: #656d76; font-variant-numeric: tabular-nums; }}
.heat {{ display: flex; flex-direction: column; gap: 6px; overflow-x: auto; }}
.heat-year {{ display: flex; align-items: center; gap: .5rem; }}
.heat-year-label {{ width: 44px; font-size: .78rem; color: #656d76; }}
.heat-cell {{ display: inline-block; width: 11px; height: 11px; border-radius: 2px; }}
.legend {{ font-size: .75rem; color: #656d76; }}
svg rect {{ stroke: rgba(27,31,36,.06); stroke-width: 1; }}
@media (prefers-color-scheme: dark) {{
  body {{ background: #0d1117; color: #e6edf3; }}
  .card, section {{ background: #161b22; border-color: #30363d; }}
  .sub, .card-label, .bar-value, .heat-year-label, .legend {{ color: #8b949e; }}
  .bar-label {{ color: #c9d1d9; }}
  .bar-track {{ background: #21262d; }}
}}
</style></head>
<body>
<h1>Photos metadata report</h1>
<div class="sub">{html.escape(ds.source)} &middot; {date_range}</div>
<div class="cards">
{_card("Assets", f"{ds.total:,}")}
{_card("Photos", f"{ds.photos:,}")}
{_card("Videos", f"{ds.videos:,}")}
{_card("With GPS", f"{pct(ds.with_gps)}")}
{_card("With people", f"{pct(ds.with_people)}")}
{_card("Favorites", f"{ds.favorites:,}")}
</div>
{block("Assets per year", _chrono(ds.years))}
{block("Time of day (local hour)", _hour_rows(ds.hours))}
{block("Day of week", _weekday_rows(ds.weekdays))}
{block("Top cameras", _ranked(ds.cameras, top))}
{block("Top countries", _ranked(ds.countries, top))}
{block("Top people", _ranked(ds.persons, top))}
{block("Top labels", _ranked(ds.labels, top))}
{block("Top albums", _ranked(ds.albums, top))}
<section><h2>Calendar heatmap (local capture date)</h2>{_heatmap(ds)}</section>
</body></html>
"""
    path.write_text(doc, encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", type=Path, default=Path("out/photos.sqlite"),
                        help="photos.sqlite or photos.json (default: out/photos.sqlite)")
    parser.add_argument("--html", type=Path, default=None,
                        help="Write a self-contained HTML report here")
    parser.add_argument("--top", type=int, default=15,
                        help="Rows per ranking section (default: 15)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.input.exists():
        print(f"Input not found: {args.input}", file=sys.stderr)
        return 1
    ds = load(args.input)
    print_report(ds, args.top)
    if args.html:
        write_html(ds, args.html, args.top)
        print(f"\nWrote {args.html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
