# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 ctkolb
"""Contract tests: keep the docs, the CLIs, and the example config in sync.

These catch documentation drift — a flag documented in ``AGENTS.md`` or
``README.md`` that no CLI implements, a config key the code does not know, or a
``config.example.yaml`` that has fallen out of step with the defaults.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import re
from pathlib import Path

import yaml

import analyze_photos_metadata
import build_journal
import export_photos_metadata
from photosmeta import leaks

ROOT = Path(__file__).resolve().parent.parent

# A flag written in a markdown code span, e.g. `--out PATH` or `--no-narrative`.
# The trailing lookahead drops the `--no-…` shorthand used in README.md.
DOC_FLAG_RE = re.compile(r"`(--[A-Za-z0-9][A-Za-z0-9-]*[A-Za-z0-9])(?![A-Za-z0-9-])")


def _flags_from(build) -> set[str]:
    """Collect every option string an argparse parser defines.

    ``build`` constructs and parses a parser (via a module's ``parse_args``).
    ``ArgumentParser.add_argument`` is wrapped so each call records its option
    strings — including the ``--no-…`` forms ``BooleanOptionalAction`` adds.
    """
    flags: set[str] = set()
    original = argparse.ArgumentParser.add_argument

    def spy(self, *args, **kwargs):
        action = original(self, *args, **kwargs)
        flags.update(action.option_strings)
        return action

    argparse.ArgumentParser.add_argument = spy
    try:
        build()
    finally:
        argparse.ArgumentParser.add_argument = original
    return flags


def _cli_flags() -> set[str]:
    flags: set[str] = set()
    for module in (export_photos_metadata, analyze_photos_metadata, build_journal):
        flags |= _flags_from(lambda module=module: module.parse_args([]))
    # leaks.main returns right after handling --hash, so this only builds its
    # parser (no files are scanned); stdout is suppressed to keep tests quiet.
    with contextlib.redirect_stdout(io.StringIO()):
        flags |= _flags_from(lambda: leaks.main(["--hash", "example"]))
    return flags


def _documented_flags() -> dict[str, str]:
    documented: dict[str, str] = {}
    for name in ("AGENTS.md", "README.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        for flag in DOC_FLAG_RE.findall(text):
            documented.setdefault(flag, name)
    return documented


def test_documented_flags_are_implemented():
    defined = _cli_flags()
    documented = _documented_flags()
    missing = sorted(flag for flag in documented if flag not in defined)
    assert not missing, f"documented but not implemented: {missing}"


def test_config_keys_match_defaults():
    assert set(build_journal.CONFIG_KEYS) == set(build_journal.DEFAULTS)


def test_config_keys_are_cli_flags():
    flags = _flags_from(lambda: build_journal.parse_args([]))
    for key in build_journal.CONFIG_KEYS:
        assert key == "inputs" or f"--{key}" in flags, key


def test_config_example_matches_defaults():
    path = ROOT / "config.example.yaml"
    assert path.is_file(), "config.example.yaml is referenced by .gitignore and leaks.py"
    data = build_journal.load_config(path)
    assert set(data) == set(build_journal.DEFAULTS)


def test_generate_config_writes_default_keys(tmp_path):
    path = tmp_path / "config.yaml"
    code = build_journal.main(["--generate-config", str(path), "--quiet"])
    assert code == 0
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert set(data) == set(build_journal.DEFAULTS)
