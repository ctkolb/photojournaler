# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 ctkolb
"""Fail when personal data would be committed.

Two independent checks run over a set of files:

1. **Path guard** — data-bearing files (``*.sqlite``, ``*.json``, ``*.csv``,
   ``*.jpg``, ``*.heic``, …) are refused outside ``tests/fixtures/``.
2. **Denylist guard** — forbidden terms (a town, a real name, home
   coordinates) are refused anywhere. The list itself must never be committed,
   so it is read at runtime from outside the repo, or stored as ``sha256:``
   hashes. Plain entries match case-insensitively as substrings; hashed
   entries match individual tokens, so multi-word terms can be hashed too.

Run it as ``python -m photosmeta.leaks --staged`` (pre-commit) or
``python -m photosmeta.leaks --all`` (CI). The denylist is read from
``--denylist`` / ``PHOTOJOURNALER_DENYLIST_FILE`` (one entry per line, ``#``
comments, ``sha256:<hex>`` for hashes) and inline from
``PHOTOJOURNALER_DENYLIST`` (entries separated by newlines, so entries may
contain commas, as coordinates do). With no denylist configured the path
guard still runs.

Print hashed entries with ``python -m photosmeta.leaks --hash TERM``.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

FORBIDDEN_SUFFIXES = (
    ".sqlite",
    ".sqlite-wal",
    ".sqlite-shm",
    ".json",
    ".csv",
    ".jpg",
    ".jpeg",
    ".heic",
    ".heif",
    ".png",
    ".tif",
    ".tiff",
    ".mov",
    ".mp4",
    ".photoslibrary",
)

FIXTURE_PARTS = ("tests", "fixtures")
_TOKEN_RE = re.compile(r"[A-Za-z0-9@._'-]+")


def _parts(path: str) -> tuple[str, ...]:
    return Path(path).parts


def _under_fixtures(parts: tuple[str, ...]) -> bool:
    for i in range(len(parts) - 1):
        if parts[i] == FIXTURE_PARTS[0] and parts[i + 1] == FIXTURE_PARTS[1]:
            return True
    return False


def is_forbidden_path(path: str, suffixes: tuple[str, ...] = FORBIDDEN_SUFFIXES) -> bool:
    """True when ``path`` is a data file outside ``tests/fixtures/``."""
    if _under_fixtures(_parts(path)):
        return False
    lowered = path.lower()
    return any(lowered.endswith(suffix) for suffix in suffixes)


def hash_token(token: str) -> str:
    """Return the ``sha256:`` form used for hashed denylist entries."""
    return "sha256:" + hashlib.sha256(token.lower().encode("utf-8")).hexdigest()


def parse_denylist(text: str) -> tuple[list[str], set[str]]:
    """Split denylist ``text`` into literal terms and hashed token hashes."""
    literals: list[str] = []
    hashed: set[str] = set()
    for line in text.splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        if entry.startswith("sha256:"):
            hashed.add(entry.lower())
        else:
            literals.append(entry)
    return literals, hashed


def load_denylist(
    path: Path | None = None, inline: str | None = None
) -> tuple[list[str], set[str]]:
    """Read the denylist from a file and/or the inline environment value."""
    text = ""
    if path is not None and path.is_file():
        text += path.read_text(encoding="utf-8", errors="replace") + "\n"
    if inline:
        text += inline + "\n"
    return parse_denylist(text)


def _default_denylist_path() -> Path | None:
    override = os.environ.get("PHOTOJOURNALER_DENYLIST_FILE")
    if override:
        return Path(override)
    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_home) if config_home else Path.home() / ".config"
    return base / "photojournaler" / "denylist.txt"


def scan_text(
    path: str, text: str, literals: list[str], hashed: set[str]
) -> list[str]:
    """Return human-readable findings for one file's contents."""
    findings: list[str] = []
    lowered = text.lower()
    for term in literals:
        if term.lower() in lowered:
            findings.append(f"{path}: matches a denylisted term")

    if hashed:
        seen: set[str] = set()
        for lineno, line in enumerate(text.splitlines(), start=1):
            for token in _TOKEN_RE.findall(line):
                digest = hash_token(token)
                if digest in hashed and digest not in seen:
                    seen.add(digest)
                    findings.append(
                        f"{path}:{lineno}: matches a hashed denylist entry"
                    )
    return findings


def _git_lines(*args: str) -> list[str]:
    result = subprocess.run(
        ("git",) + args, capture_output=True, text=True, check=True
    )
    return [line for line in result.stdout.splitlines() if line]


def collect_files(
    files: list[str], staged: bool, all_files: bool
) -> list[str]:
    """Resolve the file list for a run from CLI flags and git."""
    if files:
        return files
    if staged:
        return _git_lines("diff", "--cached", "--name-only", "--diff-filter=ACM")
    if all_files:
        return _git_lines("ls-files")
    return _git_lines("ls-files")


def check(
    files: list[str],
    literals: list[str],
    hashed: set[str],
    root: Path = Path("."),
) -> list[str]:
    """Run both guards over ``files`` and return any violations."""
    violations: list[str] = []
    for name in files:
        if is_forbidden_path(name):
            violations.append(f"{name}: data file outside tests/fixtures/")
            continue
        path = root / name
        if not path.is_file():
            continue
        raw = path.read_bytes()
        if b"\x00" in raw:
            continue
        text = raw.decode("utf-8", errors="replace")
        violations.extend(scan_text(name, text, literals, hashed))
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refuse personal data, generated exports, and secrets.",
    )
    parser.add_argument("files", nargs="*", help="paths to check (default: git files)")
    parser.add_argument("--staged", action="store_true", help="check staged files")
    parser.add_argument("--all", action="store_true", help="check all tracked files")
    parser.add_argument(
        "--denylist", type=Path, default=None, help="denylist file (kept outside the repo)"
    )
    parser.add_argument(
        "--hash", action="append", metavar="TERM",
        help="print sha256 hashes for TERM and exit",
    )
    args = parser.parse_args(argv)

    if args.hash:
        for term in args.hash:
            for token in _TOKEN_RE.findall(term) or [term]:
                print(f"{hash_token(token)}  # {term}")
        return 0

    denylist_path = args.denylist or _default_denylist_path()
    literals, hashed = load_denylist(
        denylist_path, os.environ.get("PHOTOJOURNALER_DENYLIST")
    )
    if not literals and not hashed:
        print("No denylist configured; running the path guard only.", file=sys.stderr)

    files = collect_files(args.files, args.staged, args.all)
    violations = check(files, literals, hashed)

    for violation in violations:
        print(f"error: {violation}", file=sys.stderr)
    if violations:
        print(
            f"\n{len(violations)} personal-data finding(s); commit blocked.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
