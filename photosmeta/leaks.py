# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 ctkolb
"""Fail when personal data would be committed.

Five checks keep personal data out of the repository:

1. **Path guard** — data-bearing files (``*.sqlite``, ``*.json``, ``*.csv``,
   ``*.jpg``, ``*.heic``, …), credentials (``*.pem``, ``*.key``, ``.env``), and
   denylist copies (``denylist*.txt``) are refused outside ``tests/fixtures/``,
   and any file over ``MAX_FILE_BYTES`` (1 MiB) is refused anywhere.
2. **Denylist guard** — forbidden terms (a town, a real name, home
   coordinates) are refused anywhere. The list itself must never be committed,
   so it is read at runtime from outside the repo, or stored as ``sha256:``
   hashes. Plain entries match case-insensitively as substrings; hashed
   entries match individual tokens, so multi-word terms can be hashed too.
   A token also matches without surrounding or joining punctuation, so
   ``Fairview.`` and ``Fairview's`` match a hash of ``Fairview``.
3. **Pattern guard** — machine-specific and personal patterns (macOS and Linux
   home paths, ``Library/Mobile``, ``iCloud~md``, private-key blocks) are
   refused anywhere. The guard and its tests hold literal examples, so they are
   exempt.
4. **``.gitignore`` self-test** (``--gitignore``) — the generated-output and
   data paths are still ignored, and the app files are not.
5. **History guard** (``--history``) — denylisted terms in the branch name and
   in the commit messages since the default branch.

Run it as ``python -m photosmeta.leaks --staged`` (pre-commit) or
``python -m photosmeta.leaks --full`` (all tracked files, the ``.gitignore``
self-test, and history; used by the pre-push hook). ``--all`` runs the content
guards over every tracked file, and ``--gitignore`` / ``--history`` may be
combined on their own.

The denylist is read from ``--denylist`` / ``PHOTOJOURNALER_DENYLIST_FILE`` (one
entry per line, ``#`` comments, ``sha256:<hex>`` for hashes) and inline from
``PHOTOJOURNALER_DENYLIST`` (entries separated by newlines, so entries may
contain commas, as coordinates do). With no denylist configured the path and
pattern guards still run; ``--require-denylist`` (pre-push and CI) fails instead.

Print hashed entries with ``python -m photosmeta.leaks --hash TERM``.
"""

from __future__ import annotations

import argparse
import fnmatch
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
    ".m4v",
    ".avi",
    ".3gp",
    ".gif",
    ".webp",
    # Camera RAW and Photos edit sidecars.
    ".dng",
    ".cr2",
    ".cr3",
    ".nef",
    ".arw",
    ".aae",
    # Location tracks.
    ".gpx",
    ".kml",
    ".kmz",
    # Other databases and archives that could carry an export.
    ".db",
    ".sqlite3",
    ".zip",
    ".tar",
    ".gz",
    ".7z",
    ".photoslibrary",
    # Credentials.
    ".pem",
    ".key",
    ".p12",
    ".pfx",
)
# File names refused outside tests/fixtures/ whatever their suffix: the
# denylist (its hashed entries match nothing, so a content scan cannot catch
# a committed copy) and environment files.
FORBIDDEN_NAMES = ("denylist*.txt", ".env", ".env.*")

# Any tracked file larger than this is refused: source and docs stay far
# below it, so a large file is almost always an export or media.
MAX_FILE_BYTES = 1024 * 1024

FIXTURE_PARTS = ("tests", "fixtures")
_TOKEN_RE = re.compile(r"[A-Za-z0-9@._'-]+")
# Characters a token may carry around a word (``Fairview.``, ``Fairview's``,
# ``Fairview-bound``); hashed entries also match the token without them.
_TOKEN_PUNCT = "._'-"
_TOKEN_SPLIT_RE = re.compile(r"[._'-]+")

# Machine-specific and personal patterns refused anywhere in tracked text.
# The guard and its tests intentionally hold literal examples, so they are the
# decoys the scan must not flag.
MACHINE_PATTERNS = (
    (re.compile(r"/Users/"), "macOS home path"),
    (re.compile(r"/home/"), "Linux home path"),
    (re.compile(r"Library/Mobile"), "macOS Library path"),
    (re.compile(r"iCloud~md"), "iCloud vault path"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key block"),
)
PATTERN_EXEMPT = ("photosmeta/leaks.py", "tests/test_check_leaks.py")

# Generated-output and data paths that must stay ignored, and app files that
# must stay tracked. Verified against git by the ``.gitignore`` self-test.
MUST_BE_IGNORED = (
    ".venv/pyvenv.cfg",
    "out/photos.sqlite",
    "photos.sqlite",
    "photos.sqlite-wal",
    "photos.sqlite-shm",
    "photosmeta/__pycache__/writer.cpython-312.pyc",
    ".DS_Store",
    "report.html",
    "out/report.html",
    "photos.json",
    "photos.csv",
    "journal.md",
    "journal.html",
    "photos/example.jpg",
    "vault/notes.md",
    "config.yaml",
    "config.yml",
    "Our.photoslibrary/database",
    "CLAUDE.md",
    "opencode.jsonc",
    ".env",
    ".env.local",
    "server.pem",
    "signing.key",
    "cert.p12",
    "cert.pfx",
    "denylist.txt",
    "denylist-ci.txt",
)
MUST_BE_TRACKED = (
    "photosmeta/leaks.py",
    "photosmeta/journal.py",
    "export_photos_metadata.py",
    "tests/test_check_leaks.py",
    "docs/roadmap.md",
    "README.md",
    "AGENTS.md",
    "LICENSE",
    "requirements.txt",
    ".githooks/pre-commit",
    ".githooks/pre-push",
    ".github/workflows/ci.yml",
    "config.example.yaml",
)


def _parts(path: str) -> tuple[str, ...]:
    return Path(path).parts


def _under_fixtures(parts: tuple[str, ...]) -> bool:
    for i in range(len(parts) - 1):
        if parts[i] == FIXTURE_PARTS[0] and parts[i + 1] == FIXTURE_PARTS[1]:
            return True
    return False


def is_forbidden_path(path: str, suffixes: tuple[str, ...] = FORBIDDEN_SUFFIXES) -> bool:
    """True when ``path`` is a data or credential file outside ``tests/fixtures/``."""
    parts = _parts(path)
    if _under_fixtures(parts):
        return False
    lowered = path.lower()
    name = parts[-1].lower() if parts else lowered
    return any(lowered.endswith(suffix) for suffix in suffixes) or any(
        fnmatch.fnmatchcase(name, pattern) for pattern in FORBIDDEN_NAMES
    )


def hash_token(token: str) -> str:
    """Return the ``sha256:`` form used for hashed denylist entries."""
    return "sha256:" + hashlib.sha256(token.lower().encode("utf-8")).hexdigest()


def token_variants(token: str) -> set[str]:
    """Return ``token`` plus its forms without surrounding or joining punctuation.

    ``Fairview.`` also yields ``Fairview``, and ``Fairview's`` / ``Fairview-bound``
    also yield ``Fairview``, so a hashed word still matches in running text.
    """
    variants = {token, token.strip(_TOKEN_PUNCT)}
    variants.update(_TOKEN_SPLIT_RE.split(token))
    variants.discard("")
    return variants


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
                for variant in token_variants(token):
                    digest = hash_token(variant)
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
        # R and T included: a rename or type change can introduce a data file.
        return _git_lines("diff", "--cached", "--name-only", "--diff-filter=ACMRT")
    if all_files:
        return _git_lines("ls-files")
    return _git_lines("ls-files")


def scan_patterns(path: str, text: str) -> list[str]:
    """Return findings for machine-specific or personal patterns in ``text``."""
    findings: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for rx, label in MACHINE_PATTERNS:
            if rx.search(line):
                findings.append(f"{path}:{lineno}: possible {label}")
    return findings


def read_worktree(name: str, root: Path = Path(".")) -> bytes | None:
    """Return a file's working-tree bytes, or ``None`` if it is not a file."""
    path = root / name
    return path.read_bytes() if path.is_file() else None


def read_staged(name: str, root: Path = Path(".")) -> bytes | None:
    """Return a file's staged (index) bytes — what the commit will contain."""
    result = subprocess.run(
        ("git", "-C", str(root), "show", f":{name}"), capture_output=True
    )
    return result.stdout if result.returncode == 0 else None


def check(
    files: list[str],
    literals: list[str],
    hashed: set[str],
    root: Path = Path("."),
    staged: bool = False,
) -> list[str]:
    """Run the content guards over ``files`` and return any violations.

    With ``staged`` the index copy is scanned, so a partial ``git add -p``
    is checked as it will be committed rather than as it sits on disk.
    """
    read = read_staged if staged else read_worktree
    violations: list[str] = []
    for name in files:
        if is_forbidden_path(name):
            violations.append(f"{name}: data file outside tests/fixtures/")
            continue
        raw = read(name, root)
        if raw is None:
            continue
        if len(raw) > MAX_FILE_BYTES:
            violations.append(
                f"{name}: {len(raw)} bytes, over the {MAX_FILE_BYTES} byte limit"
            )
            continue
        if b"\x00" in raw:
            continue
        text = raw.decode("utf-8", errors="replace")
        violations.extend(scan_text(name, text, literals, hashed))
        if name not in PATTERN_EXEMPT:
            violations.extend(scan_patterns(name, text))
    return violations


def gitignore_selftest(is_ignored) -> list[str]:
    """Check the output and app paths against ``is_ignored``.

    ``is_ignored`` is a ``path -> bool`` callable (git-backed in ``main``), so
    the rule set can be exercised without a repository.
    """
    findings: list[str] = []
    for path in MUST_BE_IGNORED:
        if not is_ignored(path):
            findings.append(f"{path}: expected to be gitignored but is not")
    for path in MUST_BE_TRACKED:
        if is_ignored(path):
            findings.append(f"{path}: expected to be tracked but is gitignored")
    return findings


def _git_capture(*args: str, root: Path = Path(".")) -> subprocess.CompletedProcess:
    return subprocess.run(
        ("git", "-C", str(root)) + args, capture_output=True, text=True, check=False
    )


def _git_is_ignored(path: str, root: Path = Path(".")) -> bool:
    result = subprocess.run(
        ("git", "-C", str(root), "check-ignore", "-q", "--no-index", "--", path),
        capture_output=True,
    )
    return result.returncode == 0


def scan_history(
    literals: list[str], hashed: set[str], root: Path = Path(".")
) -> list[str]:
    """Scan the branch name and commits since the default branch for terms."""
    findings: list[str] = []

    # A pull-request checkout in CI is a detached HEAD; GitHub names the
    # source branch in GITHUB_HEAD_REF instead.
    branch = os.environ.get("GITHUB_HEAD_REF") or _git_capture(
        "rev-parse", "--abbrev-ref", "HEAD", root=root
    ).stdout.strip()
    if branch:
        findings.extend(scan_text("branch name", branch, literals, hashed))

    base = None
    for candidate in ("origin/main", "main", "origin/master", "master"):
        if _git_capture(
            "rev-parse", "--verify", "--quiet", candidate, root=root
        ).returncode == 0:
            base = candidate
            break
    if base is None:
        return findings

    messages = _git_capture("log", f"{base}..HEAD", "--format=%B", root=root)
    if messages.returncode == 0 and messages.stdout:
        findings.extend(
            scan_text(
                f"commit messages ({base}..HEAD)",
                messages.stdout,
                literals,
                hashed,
            )
        )
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refuse personal data, generated exports, and secrets.",
    )
    parser.add_argument("files", nargs="*", help="paths to check (default: git files)")
    parser.add_argument("--staged", action="store_true", help="check staged files")
    parser.add_argument("--all", action="store_true", help="check all tracked files")
    parser.add_argument(
        "--gitignore", action="store_true", help="self-test the .gitignore rules"
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="scan the branch name and commits for denylisted terms",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="all tracked files, the .gitignore self-test, and history",
    )
    parser.add_argument(
        "--denylist", type=Path, default=None, help="denylist file (kept outside the repo)"
    )
    parser.add_argument(
        "--require-denylist",
        action="store_true",
        help="fail when no denylist is configured (used by pre-push and CI)",
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

    if args.full:
        args.all = True
        args.gitignore = True
        args.history = True

    denylist_path = args.denylist or _default_denylist_path()
    literals, hashed = load_denylist(
        denylist_path, os.environ.get("PHOTOJOURNALER_DENYLIST")
    )
    missing_denylist = not literals and not hashed
    if missing_denylist and not args.require_denylist:
        print(
            "No denylist configured; running the path and pattern guards only.",
            file=sys.stderr,
        )

    files = collect_files(args.files, args.staged, args.all)
    violations = check(files, literals, hashed, staged=args.staged and not args.files)
    if missing_denylist and args.require_denylist:
        violations.append(
            "no denylist configured (--require-denylist): create "
            "~/.config/photojournaler/denylist.txt or set "
            "PHOTOJOURNALER_DENYLIST / PHOTOJOURNALER_DENYLIST_FILE"
        )
    if args.gitignore:
        violations.extend(gitignore_selftest(_git_is_ignored))
    if args.history:
        violations.extend(scan_history(literals, hashed))

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
