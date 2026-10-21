# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 ctkolb
"""Tests for photosmeta.leaks (the personal-data pre-commit/CI guard)."""

from __future__ import annotations

from pathlib import Path

from photosmeta import leaks


def test_is_forbidden_path_flags_data_files():
    assert leaks.is_forbidden_path("photos.sqlite")
    assert leaks.is_forbidden_path("data/photos.json")
    assert leaks.is_forbidden_path("out/export.csv")
    assert leaks.is_forbidden_path("IMG_0001.HEIC")
    assert leaks.is_forbidden_path("clip.mp4")


def test_is_forbidden_path_allows_fixtures_and_source():
    assert not leaks.is_forbidden_path("tests/fixtures/photos.json")
    assert not leaks.is_forbidden_path("tests/fixtures/photo.jpg")
    assert not leaks.is_forbidden_path("photosmeta/journal.py")
    assert not leaks.is_forbidden_path(".gitignore")


def test_parse_denylist_splits_literals_and_hashes():
    literals, hashed = leaks.parse_denylist(
        "# comment\nFairview\n\nsha256:ABC123\n"
    )
    assert literals == ["Fairview"]
    assert hashed == {"sha256:abc123"}


def test_hash_token_is_stable_and_lowercase():
    assert leaks.hash_token("Fairview") == leaks.hash_token("fairview")
    assert leaks.hash_token("Fairview").startswith("sha256:")


def test_scan_text_literal_matches_case_insensitively():
    findings = leaks.scan_text("notes.md", "we lived in Fairview", ["fairview"], set())
    assert len(findings) == 1
    assert findings[0].startswith("notes.md:")


def test_scan_text_hashed_matches_tokens_with_line_number():
    hashed = {leaks.hash_token("Riley")}
    text = "first line\nwith Riley here\n"
    findings = leaks.scan_text("story.md", text, [], hashed)
    assert findings == ["story.md:2: matches a hashed denylist entry"]


def test_scan_text_hash_does_not_fire_on_substring():
    hashed = {leaks.hash_token("Fair")}
    findings = leaks.scan_text("notes.md", "Fairview is nice", [], hashed)
    assert findings == []


def test_check_reports_data_files_without_reading(tmp_path: Path):
    (tmp_path / "photos.sqlite").write_bytes(b"")
    violations = leaks.check(
        ["photos.sqlite", "README.md"], [], set(), root=tmp_path
    )
    assert violations == ["photos.sqlite: data file outside tests/fixtures/"]


def test_check_scans_text_files(tmp_path: Path):
    (tmp_path / "notes.md").write_text("home in Fairview", encoding="utf-8")
    violations = leaks.check(["notes.md"], ["Fairview"], set(), root=tmp_path)
    assert violations == ["notes.md: matches a denylisted term"]


def test_check_skips_binary_files(tmp_path: Path):
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01Fairview")
    violations = leaks.check(["blob.bin"], ["Fairview"], set(), root=tmp_path)
    assert violations == []


def test_load_denylist_merges_file_and_inline(tmp_path: Path):
    path = tmp_path / "denylist.txt"
    path.write_text("Fairview\n", encoding="utf-8")
    literals, hashed = leaks.load_denylist(path, "37.7749,-122.4194")
    assert literals == ["Fairview", "37.7749,-122.4194"]
    assert hashed == set()


def test_load_denylist_keeps_coordinate_commas():
    literals, _ = leaks.load_denylist(None, "37.7749,-122.4194")
    assert literals == ["37.7749,-122.4194"]
