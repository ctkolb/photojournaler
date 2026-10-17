#!/usr/bin/env python3
"""Build a markdown photo journal from exported Photos metadata.

Reads one or more ``photos.sqlite`` (or ``photos.json``) exports produced by
``export_photos_metadata.py`` and writes a single, chronological markdown
journal containing only the library owner's camera photos.

The owner's phone is inferred from the data: each month the most common iPhone
is treated as the owner's, and that model is only ever allowed to get newer
(higher generation, then higher tier). Everything else -- other people's
phones, DSLRs, screenshots, screen recordings, and saved/shared images -- is
left out.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from photosmeta.journal import (
    DEFAULT_GAP_MINUTES,
    DEFAULT_INHERIT_MINUTES,
    DEFAULT_LIBRARY,
    DEFAULT_LOCATION_DEGREES,
    DEFAULT_SIMILARITY,
    DEFAULT_THUMB_SIZE,
    LINK_MODES,
    build_journal,
    format_owner_phones,
    infer_owner_windows,
    read_photos,
    select_photos,
)


def default_inputs(outdir: Path = Path("out")) -> list[Path]:
    """The conventional export location, ``out/photos.sqlite``."""
    root = outdir / "photos.sqlite"
    return [root] if root.exists() else []


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("inputs", nargs="*", type=Path,
                        help="photos.sqlite / photos.json exports "
                             "(default: out/photos.sqlite)")
    parser.add_argument("--out", type=Path, default=Path("out/journal.md"),
                        help="Output path; .html uses a browser-native page "
                             "(default: out/journal.md)")
    parser.add_argument("--gap-minutes", type=int, default=DEFAULT_GAP_MINUTES,
                        help=f"Split a batch after this idle gap "
                             f"(default: {DEFAULT_GAP_MINUTES})")
    parser.add_argument("--similarity", type=float, default=DEFAULT_SIMILARITY,
                        help="Caption-similarity threshold for grouping "
                             f"(default: {DEFAULT_SIMILARITY})")
    parser.add_argument("--location-degrees", type=float,
                        default=DEFAULT_LOCATION_DEGREES,
                        help="Start a new place after moving this many degrees "
                             f"(~0.5 km; default: {DEFAULT_LOCATION_DEGREES})")
    parser.add_argument("--link", choices=LINK_MODES, default="thumb",
                        help="Make each photo count a link: thumb (a small "
                             "preview copied next to the journal; default), "
                             "preview/original (file:// into the library), "
                             "photos (Photos app), none")
    parser.add_argument("--library", type=Path, default=DEFAULT_LIBRARY,
                        help="Photos library path, used to resolve photo links "
                             f"(default: {DEFAULT_LIBRARY})")
    parser.add_argument("--thumb-size", type=int, default=DEFAULT_THUMB_SIZE,
                        help="Max pixels (long edge) for --link thumb "
                             f"(default: {DEFAULT_THUMB_SIZE})")
    parser.add_argument("--keep-junk", action="store_true",
                        help="Keep low-value shots (documents, barcodes, "
                             "receipts, menus, boxes); they are dropped by default")
    parser.add_argument("--no-narrative", action="store_false", dest="narrative",
                        help="Omit the per-day summary paragraph")
    parser.add_argument("--no-owner", action="store_false", dest="owner",
                        help="Include every camera (not just the owner's phone)")
    parser.add_argument("--inherit-minutes", type=int,
                        default=DEFAULT_INHERIT_MINUTES,
                        help="Give a photo with no location the nearest located "
                             "photo's location within this many minutes "
                             f"(default: {DEFAULT_INHERIT_MINUTES}; 0 disables)")
    parser.add_argument("--owner-name", action="append", default=None,
                        help="Person name to omit from 'with ...' (the journal "
                             "owner; repeatable)")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress summary output")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    inputs = args.inputs or default_inputs()
    if not inputs:
        print("No input exports found (looked under ./out)", file=sys.stderr)
        return 1
    missing = [path for path in inputs if not path.exists()]
    if missing:
        for path in missing:
            print(f"Input not found: {path}", file=sys.stderr)
        return 1

    photos = read_photos(inputs)
    windows = infer_owner_windows(photos) if args.owner else []
    selected = select_photos(photos, windows, args.owner, args.keep_junk,
                             args.inherit_minutes)
    html = args.out.suffix.lower() in (".html", ".htm")
    thumb_dir = args.out.parent / "photos" if args.link == "thumb" else None
    owner_names = set(args.owner_name) if args.owner_name else None
    document = build_journal(
        photos, args.gap_minutes, args.similarity, args.location_degrees,
        args.link, args.library, html, thumb_dir, args.thumb_size, args.keep_junk,
        args.narrative, owner_names, args.owner, args.inherit_minutes,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(document, encoding="utf-8")

    if not args.quiet:
        print(f"Read {len(photos):,} assets from {len(inputs)} export(s)")
        if args.owner:
            print(f"Owner's phone: {format_owner_phones(windows)}")
        else:
            print("Owner-phone detection: off (all cameras)")
        print(f"Selected {len(selected):,} photos -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
