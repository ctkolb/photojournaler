# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 ctkolb
"""Tests for photosmeta.extract.build_record."""

from __future__ import annotations

import datetime as dt

import pytest

from photosmeta.extract import EXIF_FIELDS, FLAG_FIELDS, build_record

from conftest import FakeSearchInfo, make_album, make_exif, make_photo, make_place


class Exploding:
    """Fails loudly if any attribute is accessed."""

    def __getattr__(self, name):
        raise AssertionError(f"search_info accessed via {name!r}")

    def asdict(self):
        raise AssertionError("search_info.asdict accessed")


def test_core_fields_and_media_type():
    photo = make_photo(uuid="abc", filename="x.HEIC", isphoto=True)
    record = build_record(photo)
    assert record["uuid"] == "abc"
    assert record["filename"] == "x.HEIC"
    assert record["media_type"] == "photo"


def test_video_media_type():
    photo = make_photo(isphoto=False, ismovie=True)
    assert build_record(photo)["media_type"] == "video"


def test_dates_are_iso_with_offset():
    record = build_record(make_photo())
    assert record["date"] == "2023-05-04T10:30:00-05:00"
    assert record["date_original"].startswith("2023-05-04T10:30:00")


def test_none_dates_are_none():
    photo = make_photo(date=None, date_original=None, date_added=None, date_modified=None)
    record = build_record(photo)
    assert record["date"] is None
    assert record["date_modified"] is None


def test_none_place_yields_none():
    assert build_record(make_photo(place=None))["place"] is None


def test_place_is_extracted():
    record = build_record(make_photo(place=make_place(name="Paris, FR", country_code="FR")))
    assert record["place"]["name"] == "Paris, FR"
    assert record["place"]["country_code"] == "FR"
    assert record["place"]["address"] == "1 Main St"


def test_missing_exif_info_gives_all_none():
    record = build_record(make_photo(exif_info=None))
    assert set(record["exif"]) == set(EXIF_FIELDS)
    assert all(value is None for value in record["exif"].values())


def test_exif_fields_are_promoted():
    photo = make_photo(exif_info=make_exif(camera_model="iPhone 15", iso=200, aperture=1.6))
    record = build_record(photo)
    assert record["exif"]["camera_model"] == "iPhone 15"
    assert record["exif"]["iso"] == 200
    assert record["exif"]["aperture"] == 1.6


def test_all_flag_fields_present():
    record = build_record(make_photo())
    assert set(record["flags"]) == set(FLAG_FIELDS)
    assert record["flags"]["favorite"] is False


def test_flags_reflect_values():
    record = build_record(make_photo(favorite=True, live_photo=True))
    assert record["flags"]["favorite"] is True
    assert record["flags"]["live_photo"] is True


def test_score_overall_and_full_scores():
    from conftest import FakeScore

    photo = make_photo(score=FakeScore(overall=0.42, data={"overall": 0.42, "noise": 0.1}))
    record = build_record(photo)
    assert record["score"] == pytest.approx(0.42)
    assert record["scores"]["noise"] == pytest.approx(0.1)


def test_none_score():
    record = build_record(make_photo(score=None))
    assert record["score"] is None
    assert record["scores"] is None


def test_album_folders_deduped_and_joined():
    photo = make_photo(
        albums=["A", "B"],
        album_info=[
            make_album("A", ["Trips", "2023"]),
            make_album("B", ["Trips"]),
        ],
    )
    record = build_record(photo)
    assert record["albums"] == ["A", "B"]
    assert record["album_folders"] == ["Trips/2023", "Trips"]


def test_ml_included():
    photo = make_photo(
        ai_caption="a cat",
        labels_normalized=["animal"],
        search_info=FakeSearchInfo(labels=["Animal"], data={"city": "Austin"}),
        media_analysis={"image_caption": {"imageCaptionConfidence": 0.75}},
    )
    ml = build_record(photo)["ml"]
    assert ml["ai_caption"] == "a cat"
    assert ml["ai_caption_confidence"] == 0.75
    assert ml["labels"] == ["animal"]
    assert ml["search_labels"] == ["Animal"]
    assert ml["search_info"]["city"] == "Austin"


def test_ml_handles_missing_analysis_and_search_info():
    ml = build_record(make_photo(search_info=None, media_analysis=None))["ml"]
    assert ml["ai_caption_confidence"] is None
    assert ml["search_labels"] is None
    assert ml["search_info"] is None


def test_include_ml_false_skips_search_info_entirely():
    photo = make_photo(search_info=Exploding())
    record = build_record(photo, include_ml=False)
    assert record["ml"] is None


def test_include_ml_true_would_access_search_info():
    photo = make_photo(search_info=Exploding())
    with pytest.raises(AssertionError):
        build_record(photo, include_ml=True)


def test_record_is_json_serializable():
    import json

    from conftest import FakeScore

    photo = make_photo(
        score=FakeScore(),
        search_info=FakeSearchInfo(labels=["Beach"], data={"labels": ["Beach"]}),
        media_analysis={"image_caption": {"imageCaptionConfidence": 0.5}},
    )
    json.dumps(build_record(photo), default=str)
