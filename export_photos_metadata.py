#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 ctkolb
"""Export metadata from a macOS Photos library to JSON, CSV, and SQLite.

Reads the local Photos.sqlite database through osxphotos. No photos are
downloaded or modified; assets that live only in iCloud still contribute
whatever metadata the database holds.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

from osxphotos import PhotosDB

from photosmeta import build_record
from photosmeta.writers import CsvWriter, JsonWriter, SqliteWriter

ALL_FORMATS = ("json", "csv", "sqlite")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, default=None,
                        help="Path to a .photoslibrary (default: system library)")
    parser.add_argument("--outdir", type=Path, default=Path("out"),
                        help="Output directory (default: ./out)")
    parser.add_argument("--prefix", default="photos",
                        help="Output filename prefix (default: photos)")
    parser.add_argument("--formats", default="json,csv,sqlite",
                        help="Comma-separated: json,csv,sqlite (default: all)")
    parser.add_argument("--album", action="append", default=None,
                        help="Only include this album (repeatable)")
    parser.add_argument("--person", action="append", default=None,
                        help="Only include photos of this person (repeatable)")
    parser.add_argument("--keyword", action="append", default=None,
                        help="Only include this keyword (repeatable)")
    parser.add_argument("--since", type=datetime.fromisoformat, default=None,
                        help="Earliest capture date, YYYY-MM-DD (inclusive)")
    parser.add_argument("--until", type=datetime.fromisoformat, default=None,
                        help="Latest capture date, YYYY-MM-DD (inclusive)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Stop after N assets (for testing)")
    parser.add_argument("--no-ml", action="store_true",
                        help="Skip ML labels, captions, and image analysis")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress progress output")
    return parser.parse_args(argv)


def _make_writers(outdir: Path, prefix: str, formats: list[str]) -> list:
    writers: list = []
    if "json" in formats:
        writers.append(JsonWriter(outdir / f"{prefix}.json"))
    if "csv" in formats:
        writers.append(CsvWriter(outdir / f"{prefix}.csv"))
    if "sqlite" in formats:
        writers.append(SqliteWriter(outdir / f"{prefix}.sqlite"))
    return writers


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    formats = [f.strip().lower() for f in args.formats.split(",") if f.strip()]
    unknown = set(formats) - set(ALL_FORMATS)
    if unknown:
        print(f"Unknown format(s): {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2

    args.outdir.mkdir(parents=True, exist_ok=True)

    start = time.time()
    if not args.quiet:
        print("Loading Photos library...", file=sys.stderr)
    db = PhotosDB(library_path=args.library, _skip_searchinfo=args.no_ml)
    if not args.quiet:
        print(f"Loaded in {time.time() - start:.1f}s", file=sys.stderr)

    photos = db.photos(
        albums=args.album,
        persons=args.person,
        keywords=args.keyword,
        from_date=args.since,
        to_date=args.until,
    )
    if args.limit:
        photos = photos[: args.limit]
    total = len(photos)
    if not args.quiet:
        print(f"Selected {total:,} assets", file=sys.stderr)

    writers = _make_writers(args.outdir, args.prefix, formats)
    include_ml = not args.no_ml
    errors = 0
    processed = 0
    tick = max(total // 100, 1) if total else 1
    started = time.time()

    try:
        for photo in photos:
            try:
                record = build_record(photo, include_ml=include_ml)
            except Exception as exc:  # noqa: BLE001 - keep going on bad assets
                errors += 1
                if not args.quiet:
                    print(f"  skipped {getattr(photo, 'uuid', '?')}: {exc}", file=sys.stderr)
                continue
            for writer in writers:
                writer.write(record)
            processed += 1
            if not args.quiet and processed % tick == 0:
                elapsed = time.time() - started
                rate = processed / elapsed if elapsed else 0
                eta = (total - processed) / rate if rate else 0
                pct = processed / total * 100 if total else 100
                print(
                    f"  {pct:5.1f}%  {processed:,}/{total:,}  "
                    f"{rate:,.0f}/s  ETA {eta:,.0f}s",
                    file=sys.stderr,
                )
    finally:
        for writer in writers:
            writer.close()

    elapsed = time.time() - start
    if not args.quiet:
        print(
            f"Done: {processed:,} assets in {elapsed:.1f}s "
            f"({errors} skipped) -> {args.outdir}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
