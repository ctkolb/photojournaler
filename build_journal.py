#!/usr/bin/env python3
"""Build a markdown photo journal from exported Photos metadata.

Reads one or more ``photos.sqlite`` (or ``photos.json``) exports produced by
``export_photos_metadata.py`` and writes a single, chronological journal
containing only the library owner's camera photos.

Every option can also be set in a YAML config file. Flags override the config,
and the config overrides the built-in defaults. With no ``--config``, a
``config.yaml`` in the working directory is used when present. ``--generate-config``
writes a config (from the defaults, with any given flags applied) and exits.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from photosmeta.journal import (
    DEFAULT_GAP_MINUTES,
    DEFAULT_GPS_NAME_KM,
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

DEFAULT_CONFIG = Path("config.yaml")

# config/flag name -> (argparse dest, kind). kind is "value", "store_true",
# or "store_false". The YAML key is the same as the flag (without "--").
SETTINGS: dict[str, tuple[str, str]] = {
    "out": ("out", "value"),
    "gap-minutes": ("gap_minutes", "value"),
    "similarity": ("similarity", "value"),
    "location-degrees": ("location_degrees", "value"),
    "link": ("link", "value"),
    "library": ("library", "value"),
    "thumb-size": ("thumb_size", "value"),
    "no-speculative-id": ("speculative_id", "store_false"),
    "inherit-minutes": ("inherit_minutes", "value"),
    "gps-name-km": ("gps_name_km", "value"),
    "keep-junk": ("keep_junk", "store_true"),
    "no-narrative": ("narrative", "store_false"),
    "owner-name": ("owner_name", "value"),
    "quiet": ("quiet", "store_true"),
}

# Default settings, in the same "flag" space (booleans mean "flag is set").
DEFAULTS: dict = {
    "inputs": [],
    "out": "out/journal.md",
    "gap-minutes": DEFAULT_GAP_MINUTES,
    "similarity": DEFAULT_SIMILARITY,
    "location-degrees": DEFAULT_LOCATION_DEGREES,
    "link": "thumb",
    "library": str(DEFAULT_LIBRARY),
    "thumb-size": DEFAULT_THUMB_SIZE,
    "no-speculative-id": False,
    "inherit-minutes": DEFAULT_INHERIT_MINUTES,
    "gps-name-km": DEFAULT_GPS_NAME_KM,
    "keep-junk": False,
    "no-narrative": False,
    "owner-name": [],
    "quiet": False,
}


def default_inputs(outdir: Path = Path("out")) -> list[Path]:
    """The conventional export location, ``out/photos.sqlite``."""
    root = outdir / "photos.sqlite"
    return [root] if root.exists() else []


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("inputs", nargs="*", type=Path,
                        help="photos.sqlite / photos.json exports "
                             "(default: out/photos.sqlite, or 'inputs' in config)")
    parser.add_argument("--config", type=Path, default=argparse.SUPPRESS,
                        help="YAML config to use (default: ./config.yaml if present)")
    parser.add_argument("--generate-config", nargs="?", const=str(DEFAULT_CONFIG),
                        default=argparse.SUPPRESS, metavar="PATH",
                        help="Write a config (default: config.yaml) from the "
                             "defaults and any given flags, then exit")
    parser.add_argument("--out", default=argparse.SUPPRESS,
                        help="Output file; a .html path writes a browser-native page")
    parser.add_argument("--gap-minutes", type=int, default=argparse.SUPPRESS,
                        help="Split a batch after this idle gap")
    parser.add_argument("--similarity", type=float, default=argparse.SUPPRESS,
                        help="Caption-grouping threshold; lower groups more")
    parser.add_argument("--location-degrees", type=float, default=argparse.SUPPRESS,
                        help="Start a new place after moving this far, in degrees")
    parser.add_argument("--link", choices=LINK_MODES, default=argparse.SUPPRESS,
                        help="Link each count: thumb, preview, original, photos, none")
    parser.add_argument("--library", default=argparse.SUPPRESS,
                        help="Photos library path, used to resolve photo links")
    parser.add_argument("--thumb-size", type=int, default=argparse.SUPPRESS,
                        help="Max pixels (long edge) for --link thumb")
    parser.add_argument("--no-speculative-id", action="store_false",
                        dest="speculative_id", default=argparse.SUPPRESS,
                        help="Do not infer the owner's phone; include every camera")
    parser.add_argument("--inherit-minutes", type=int, default=argparse.SUPPRESS,
                        help="Give a location-less photo the nearest located "
                             "photo's place within this many minutes (0 disables)")
    parser.add_argument("--gps-name-km", type=float, default=argparse.SUPPRESS,
                        help="Name a GPS-only photo after the nearest located "
                             "photo within this many km (0 disables)")
    parser.add_argument("--keep-junk", action="store_true", default=argparse.SUPPRESS,
                        help="Keep low-value shots (documents, barcodes, receipts, "
                             "menus, boxes); they are dropped by default")
    parser.add_argument("--no-narrative", action="store_false", dest="narrative",
                        default=argparse.SUPPRESS,
                        help="Omit the per-day summary paragraph")
    parser.add_argument("--owner-name", action="append", default=argparse.SUPPRESS,
                        help="Person name to omit from 'with ...' (repeatable)")
    parser.add_argument("--quiet", action="store_true", default=argparse.SUPPRESS,
                        help="Suppress summary output")
    return parser.parse_args(argv)


def flag_overrides(args: argparse.Namespace) -> dict:
    """The settings explicitly set on the command line, in flag space."""
    overrides: dict = {}
    for flag, (dest, kind) in SETTINGS.items():
        if not hasattr(args, dest):
            continue
        overrides[flag] = getattr(args, dest) if kind == "value" else True
    if getattr(args, "inputs", None):
        overrides["inputs"] = [str(path) for path in args.inputs]
    return overrides


def load_config(path: Path) -> dict:
    """Read a YAML config, keeping only known settings."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise SystemExit(f"Config {path} must be a mapping")
    known = {key: value for key, value in data.items() if key in DEFAULTS}
    unknown = sorted(set(data) - set(known))
    if unknown:
        print(f"Ignoring unknown config keys: {', '.join(unknown)}", file=sys.stderr)
    return known


def write_config(path: Path, settings: dict) -> None:
    ordered = {key: settings[key] for key in DEFAULTS}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# photojournaler config. Every value can also be set with the matching\n"
        "# command-line flag; flags override the config, which overrides defaults.\n"
        + yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    overrides = flag_overrides(args)

    if hasattr(args, "generate_config"):
        settings = dict(DEFAULTS)
        settings.update(overrides)
        path = Path(args.generate_config)
        write_config(path, settings)
        if not settings.get("quiet"):
            print(f"Wrote {path}")
        return 0

    config_path = None
    if hasattr(args, "config"):
        config_path = Path(args.config)
        if not config_path.exists():
            print(f"Config not found: {config_path}", file=sys.stderr)
            return 1
    elif DEFAULT_CONFIG.exists():
        config_path = DEFAULT_CONFIG

    settings = dict(DEFAULTS)
    if config_path is not None:
        settings.update(load_config(config_path))
    settings.update(overrides)

    inputs = [Path(p) for p in settings["inputs"]] or default_inputs()
    if not inputs:
        print("No input exports found (looked under ./out)", file=sys.stderr)
        return 1
    missing = [path for path in inputs if not path.exists()]
    if missing:
        for path in missing:
            print(f"Input not found: {path}", file=sys.stderr)
        return 1

    owner = not settings["no-speculative-id"]
    keep_noise = settings["keep-junk"]
    narrative = not settings["no-narrative"]
    quiet = settings["quiet"]

    photos = read_photos(inputs)
    windows = infer_owner_windows(photos) if owner else []
    selected = select_photos(photos, windows, owner, keep_noise,
                             settings["inherit-minutes"], settings["gps-name-km"])
    out = Path(settings["out"])
    html = out.suffix.lower() in (".html", ".htm")
    thumb_dir = out.parent / "photos" if settings["link"] == "thumb" else None
    owner_names = set(settings["owner-name"]) or None
    document = build_journal(
        photos, settings["gap-minutes"], settings["similarity"],
        settings["location-degrees"], settings["link"], Path(settings["library"]),
        html, thumb_dir, settings["thumb-size"], keep_noise, narrative,
        owner_names, owner, settings["inherit-minutes"], settings["gps-name-km"],
    )

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(document, encoding="utf-8")

    if not quiet:
        print(f"Read {len(photos):,} assets from {len(inputs)} export(s)")
        if owner:
            print(f"Owner's phone: {format_owner_phones(windows)}")
        else:
            print("Speculative owner-phone detection: off (all cameras)")
        print(f"Selected {len(selected):,} photos -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
