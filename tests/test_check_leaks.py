# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 ctkolb
"""Tests for photosmeta.leaks (the personal-data pre-commit/CI guard)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

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


def test_scan_patterns_flags_machine_paths():
    findings = leaks.scan_patterns("code.py", 'HOME = "/Users/chris/Pictures"')
    assert findings == ["code.py:1: possible macOS home path"]

    findings = leaks.scan_patterns("code.py", "root=/home/chris, lib=Library/Mobile")
    assert "code.py:1: possible Linux home path" in findings
    assert "code.py:1: possible macOS Library path" in findings


def test_scan_patterns_flags_private_key_block():
    findings = leaks.scan_patterns("key.pem", "-----BEGIN RSA PRIVATE KEY-----")
    assert findings == ["key.pem:1: possible private key block"]


def test_scan_patterns_ignores_clean_text():
    assert leaks.scan_patterns("notes.md", "a walk by the river") == []


def test_check_exempts_guard_from_pattern_scan(tmp_path: Path):
    for name in leaks.PATTERN_EXEMPT:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("home is /Users/chris/Pictures\n", encoding="utf-8")
        assert leaks.check([name], [], set(), root=tmp_path) == []


def test_gitignore_selftest_flags_missing_ignores():
    findings = leaks.gitignore_selftest(lambda path: False)
    assert len(findings) == len(leaks.MUST_BE_IGNORED)
    assert all("expected to be gitignored" in f for f in findings)


def test_gitignore_selftest_flags_ignored_app_files():
    findings = leaks.gitignore_selftest(lambda path: True)
    assert len(findings) == len(leaks.MUST_BE_TRACKED)
    assert all("expected to be tracked" in f for f in findings)


def test_gitignore_selftest_passes_when_rules_hold():
    ignored = set(leaks.MUST_BE_IGNORED)
    assert leaks.gitignore_selftest(lambda path: path in ignored) == []


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True)


def _git_repo(tmp_path: Path) -> Path:
    """An empty repo on ``main`` with one commit and no hooks."""
    repo = tmp_path / "repo"
    repo.mkdir()
    nohooks = tmp_path / "nohooks"
    nohooks.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    for key, value in (
        ("user.email", "test@example.com"),
        ("user.name", "Test"),
        ("commit.gpgsign", "false"),
        ("core.hooksPath", str(nohooks)),
    ):
        _git(repo, "config", key, value)
    _git(repo, "commit", "--allow-empty", "-qm", "initial")
    return repo


def test_scan_history_flags_branch_and_commit_messages(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("GITHUB_HEAD_REF", raising=False)
    repo = _git_repo(tmp_path)
    _git(repo, "switch", "-q", "-c", "trip-to-Fairview")
    _git(repo, "commit", "--allow-empty", "-qm", "visit Fairview")

    findings = leaks.scan_history(["Fairview"], set(), root=repo)

    assert "branch name: matches a denylisted term" in findings
    assert "commit messages (main..HEAD): matches a denylisted term" in findings


def test_scan_history_uses_github_head_ref_on_detached_head(tmp_path: Path, monkeypatch):
    repo = _git_repo(tmp_path)
    _git(repo, "switch", "-q", "--detach")
    monkeypatch.setenv("GITHUB_HEAD_REF", "trip-to-Fairview")

    findings = leaks.scan_history(["Fairview"], set(), root=repo)

    assert "branch name: matches a denylisted term" in findings


@pytest.mark.parametrize(
    "text", ["in Fairview.", "Fairview's park", "Fairview-bound", "(Fairview)"]
)
def test_scan_text_hash_matches_through_punctuation(text):
    hashed = {leaks.hash_token("Fairview")}
    findings = leaks.scan_text("notes.md", text, [], hashed)
    assert findings == ["notes.md:1: matches a hashed denylist entry"]


def test_scan_text_hash_still_matches_punctuated_entries():
    hashed = {leaks.hash_token("41.8781")}
    findings = leaks.scan_text("notes.md", "at 41.8781, -87.6", [], hashed)
    assert findings == ["notes.md:1: matches a hashed denylist entry"]


def test_collect_files_staged_includes_renames(tmp_path: Path, monkeypatch):
    repo = _git_repo(tmp_path)
    (repo / "notes.txt").write_text("a b c d e f g h\n", encoding="utf-8")
    _git(repo, "add", "notes.txt")
    _git(repo, "commit", "-qm", "notes")
    _git(repo, "mv", "notes.txt", "export.csv")
    monkeypatch.chdir(repo)

    files = leaks.collect_files([], staged=True, all_files=False)

    assert files == ["export.csv"]
    assert leaks.check(files, [], set(), staged=True) == [
        "export.csv: data file outside tests/fixtures/"
    ]


def test_check_staged_scans_the_index_copy(tmp_path: Path):
    repo = _git_repo(tmp_path)
    story = repo / "story.md"
    story.write_text("visit Fairview\n", encoding="utf-8")
    _git(repo, "add", "story.md")
    story.write_text("visit town\n", encoding="utf-8")

    staged = leaks.check(["story.md"], ["Fairview"], set(), root=repo, staged=True)
    worktree = leaks.check(["story.md"], ["Fairview"], set(), root=repo)

    assert staged == ["story.md: matches a denylisted term"]
    assert worktree == []

