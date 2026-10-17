"""End-to-end tests for the export CLI, with osxphotos.PhotosDB faked out."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import pytest

import export_photos_metadata as cli
from conftest import FakeDB, make_photo


class Exploding:
    def __getattr__(self, name):
        raise AssertionError(f"exif accessed via {name!r}")


def install_fake_db(monkeypatch, photos):
    created = []

    def factory(**kwargs):
        db = FakeDB(photos, init_kwargs=kwargs)
        created.append(db)
        return db

    monkeypatch.setattr(cli, "PhotosDB", factory)
    return created


def test_exports_all_formats(tmp_path, monkeypatch, sample_photos):
    install_fake_db(monkeypatch, sample_photos)
    outdir = tmp_path / "out"
    code = cli.main(["--outdir", str(outdir), "--quiet"])
    assert code == 0
    assert (outdir / "photos.json").exists()
    assert (outdir / "photos.csv").exists()
    assert (outdir / "photos.sqlite").exists()
    assert len(json.loads((outdir / "photos.json").read_text())) == 3
    con = sqlite3.connect(outdir / "photos.sqlite")
    assert con.execute("SELECT COUNT(*) FROM photos").fetchone()[0] == 3


def test_prefix_is_respected(tmp_path, monkeypatch, sample_photos):
    install_fake_db(monkeypatch, sample_photos)
    outdir = tmp_path / "out"
    cli.main(["--outdir", str(outdir), "--prefix", "lib", "--formats", "json", "--quiet"])
    assert (outdir / "lib.json").exists()
    assert not (outdir / "photos.json").exists()


def test_limit(tmp_path, monkeypatch, sample_photos):
    install_fake_db(monkeypatch, sample_photos)
    outdir = tmp_path / "out"
    cli.main(["--outdir", str(outdir), "--limit", "2", "--formats", "json", "--quiet"])
    assert len(json.loads((outdir / "photos.json").read_text())) == 2


def test_format_subset(tmp_path, monkeypatch, sample_photos):
    install_fake_db(monkeypatch, sample_photos)
    outdir = tmp_path / "out"
    cli.main(["--outdir", str(outdir), "--formats", "csv", "--quiet"])
    assert (outdir / "photos.csv").exists()
    assert not (outdir / "photos.json").exists()
    assert not (outdir / "photos.sqlite").exists()


def test_unknown_format_returns_2(tmp_path, monkeypatch, sample_photos, capsys):
    install_fake_db(monkeypatch, sample_photos)
    outdir = tmp_path / "out"
    code = cli.main(["--outdir", str(outdir), "--formats", "json,parquet", "--quiet"])
    assert code == 2
    assert "parquet" in capsys.readouterr().err
    assert not outdir.exists()


def test_filters_are_forwarded(tmp_path, monkeypatch, sample_photos):
    created = install_fake_db(monkeypatch, sample_photos)
    cli.main([
        "--outdir", str(tmp_path / "out"), "--formats", "json", "--quiet",
        "--album", "A", "--album", "B", "--person", "Alice",
        "--keyword", "beach", "--since", "2020-01-01", "--until", "2024-12-31",
    ])
    kwargs = created[0].query_kwargs
    assert kwargs["albums"] == ["A", "B"]
    assert kwargs["persons"] == ["Alice"]
    assert kwargs["keywords"] == ["beach"]
    assert kwargs["from_date"] == datetime(2020, 1, 1)
    assert kwargs["to_date"] == datetime(2024, 12, 31)


def test_no_ml_sets_skip_searchinfo_and_omits_ml(tmp_path, monkeypatch, sample_photos):
    created = install_fake_db(monkeypatch, sample_photos)
    outdir = tmp_path / "out"
    cli.main(["--outdir", str(outdir), "--formats", "json", "--no-ml", "--quiet"])
    assert created[0].init_kwargs["_skip_searchinfo"] is True
    records = json.loads((outdir / "photos.json").read_text())
    assert all(record["ml"] is None for record in records)


def test_default_keeps_searchinfo(tmp_path, monkeypatch, sample_photos):
    created = install_fake_db(monkeypatch, sample_photos)
    cli.main(["--outdir", str(tmp_path / "out"), "--formats", "json", "--quiet"])
    assert created[0].init_kwargs["_skip_searchinfo"] is False
    records = json.loads((tmp_path / "out" / "photos.json").read_text())
    assert records[0]["ml"] is not None


def test_library_path_forwarded(tmp_path, monkeypatch, sample_photos):
    created = install_fake_db(monkeypatch, sample_photos)
    lib = tmp_path / "My.photoslibrary"
    cli.main(["--outdir", str(tmp_path / "out"), "--formats", "json",
              "--library", str(lib), "--quiet"])
    assert created[0].init_kwargs["library_path"] == lib


def test_bad_photo_is_skipped(tmp_path, monkeypatch, sample_photos, capsys):
    bad = make_photo(uuid="bad-uuid", exif_info=Exploding())
    photos = [sample_photos[0], bad, sample_photos[2]]
    install_fake_db(monkeypatch, photos)
    outdir = tmp_path / "out"
    code = cli.main(["--outdir", str(outdir), "--formats", "json"])
    assert code == 0
    records = json.loads((outdir / "photos.json").read_text())
    assert len(records) == 2
    assert "skipped bad-uuid" in capsys.readouterr().err


def test_photo_missing_uuid_does_not_crash(tmp_path, monkeypatch, sample_photos, capsys):
    class NoUuid:
        def __getattr__(self, name):
            raise AttributeError(name)

    install_fake_db(monkeypatch, [sample_photos[0], NoUuid()])
    outdir = tmp_path / "out"
    code = cli.main(["--outdir", str(outdir), "--formats", "json"])
    assert code == 0
    assert "skipped ?" in capsys.readouterr().err


def test_quiet_suppresses_stderr(tmp_path, monkeypatch, sample_photos, capsys):
    install_fake_db(monkeypatch, sample_photos)
    cli.main(["--outdir", str(tmp_path / "out"), "--formats", "json", "--quiet"])
    assert capsys.readouterr().err == ""


def test_empty_library_produces_empty_outputs(tmp_path, monkeypatch):
    install_fake_db(monkeypatch, [])
    outdir = tmp_path / "out"
    code = cli.main(["--outdir", str(outdir), "--quiet"])
    assert code == 0
    assert json.loads((outdir / "photos.json").read_text()) == []
    con = sqlite3.connect(outdir / "photos.sqlite")
    assert con.execute("SELECT COUNT(*) FROM photos").fetchone()[0] == 0


def test_parse_args_unknown_flag_exits():
    with pytest.raises(SystemExit):
        cli.parse_args(["--bogus"])
