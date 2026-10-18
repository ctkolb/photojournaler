"""Tests for photosmeta.journal and the build_journal CLI."""

from __future__ import annotations

import base64
import json
import shutil
from datetime import datetime, timezone, timedelta

import pytest
import yaml

import build_journal
from photosmeta import journal as j
from photosmeta import build_record
from photosmeta.writers import SqliteWriter

from conftest import make_photo, make_place

CST = timezone(timedelta(hours=-6))
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def photo(date, camera="iPhone 13 Pro", **overrides):
    fields = dict(screenshot=False, media_type="photo", favorite=False)
    fields.update(overrides)
    return j.Photo(date_original=date, camera_model=camera, **fields)


def entry(date, caption=None, labels=None, favorite=False, video=False,
          camera="iPhone 13 Pro", uuid=None, filename=None):
    return j.Entry(
        dt=datetime.fromisoformat(date), caption=caption, labels=labels,
        favorite=favorite, video=video, camera=camera, uuid=uuid, filename=filename,
    )


# --- model ranking --------------------------------------------------------

def test_model_rank_orders_generation_then_tier():
    assert j.model_rank("iPhone 16 Pro") > j.model_rank("iPhone 13 Pro")
    assert j.model_rank("iPhone 17 Pro Max") > j.model_rank("iPhone 17 Pro")
    assert j.model_rank("iPhone 17 Pro") > j.model_rank("iPhone 17")
    assert j.model_rank("iPhone 15 Plus") > j.model_rank("iPhone 15")


def test_model_rank_rejects_non_phones():
    assert j.model_rank("Canon EOS 7D") is None
    assert j.model_rank("iPhone SE (3rd generation)") is None
    assert j.model_rank(None) is None


# --- owner inference ------------------------------------------------------

def test_infer_owner_windows_is_monotonic_and_newer():
    photos = [
        photo("2024-01-05T10:00:00-06:00", "iPhone 13 Pro"),
        photo("2024-01-06T10:00:00-06:00", "iPhone 13 Pro"),
        photo("2024-09-20T10:00:00-05:00", "iPhone 16 Pro"),
        photo("2024-10-01T10:00:00-05:00", "iPhone 16 Pro"),
        photo("2026-06-07T10:00:00-05:00", "iPhone 17 Pro Max"),
        photo("2026-06-08T10:00:00-05:00", "iPhone 17 Pro Max"),
    ]
    windows = j.infer_owner_windows(photos)
    assert windows == [
        ("2024-01-05T10:00:00-06:00", "iPhone 13 Pro"),
        ("2024-09-20T10:00:00-05:00", "iPhone 16 Pro"),
        ("2026-06-07T10:00:00-05:00", "iPhone 17 Pro Max"),
    ]


def test_infer_owner_windows_ignores_family_phone_that_never_dominates():
    photos = [
        photo("2024-06-01T10:00:00-05:00", "iPhone 13 Pro"),
        photo("2024-06-02T10:00:00-05:00", "iPhone 13 Pro"),
        photo("2024-06-03T10:00:00-05:00", "iPhone 13 Pro"),
        # A newer family phone, but never the most common.
        photo("2024-06-04T10:00:00-05:00", "iPhone 15 Pro"),
    ]
    windows = j.infer_owner_windows(photos)
    assert [model for _, model in windows] == ["iPhone 13 Pro"]


def test_infer_owner_windows_ignores_dslr_only_months():
    photos = [
        photo("2024-06-01T10:00:00-05:00", "iPhone 13 Pro"),
        photo("2024-07-01T10:00:00-05:00", "Canon EOS 7D"),
        photo("2024-07-02T10:00:00-05:00", "Canon EOS 7D"),
    ]
    assert [model for _, model in j.infer_owner_windows(photos)] == ["iPhone 13 Pro"]


def test_owner_model_lookup_uses_window_start():
    windows = [("2024-01-01", "iPhone 13 Pro"), ("2024-09-20", "iPhone 16 Pro")]
    assert j.owner_model("2024-09-19T23:00:00-05:00", windows) == "iPhone 13 Pro"
    assert j.owner_model("2024-09-20T00:00:00-05:00", windows) == "iPhone 16 Pro"
    assert j.owner_model("2023-12-31T00:00:00-06:00", windows) is None


# --- filtering and selection ---------------------------------------------

def test_is_camera_photo_filters_noise():
    assert j.is_camera_photo(photo("2024-01-01T10:00:00-06:00"))
    assert not j.is_camera_photo(photo("2024-01-01T10:00:00-06:00", screenshot=True))
    assert not j.is_camera_photo(
        photo("2024-01-01T10:00:00-06:00", search_info={"media_types": ["Screenshots"]})
    )
    assert not j.is_camera_photo(
        photo("2024-01-01T10:00:00-06:00", search_info={"media_types": ["Screen Recordings"]})
    )
    assert not j.is_camera_photo(
        photo("2024-01-01T10:00:00-06:00", search_info={"source": "Messages"})
    )


def test_select_owner_photos_filters_dedups_and_sorts():
    windows = [("2024-01-01", "iPhone 13 Pro")]
    photos = [
        photo("2024-01-03T10:00:00-06:00", "iPhone 13 Pro", ai_caption="b"),
        photo("2024-01-02T10:00:00-06:00", "iPhone 13 Pro", ai_caption="a"),
        photo("2024-01-02T10:00:00-06:00", "iPhone 13 Pro", ai_caption="dup"),
        photo("2024-01-02T10:00:00-06:00", "iPhone 15 Pro", ai_caption="family"),
        photo("2024-01-02T10:00:00-06:00", "Canon EOS 7D", ai_caption="dslr"),
        photo("2024-01-02T10:00:00-06:00", "iPhone 13 Pro", screenshot=True),
    ]
    selected = j.select_photos(photos, windows)
    assert [p.ai_caption for p in selected] == ["a", "b"]


def test_is_noise_flags_documents_menus_and_boxes():
    assert j.is_noise(photo("2024-01-01T10:00:00-06:00",
                            ai_caption="a close up of a barcode on a white background"))
    assert j.is_noise(photo("2024-01-01T10:00:00-06:00",
                            ai_caption="a restaurant menu on a wooden surface"))
    assert j.is_noise(photo("2024-01-01T10:00:00-06:00",
                            ai_caption="a close up of a box of food on a table"))
    assert j.is_noise(photo("2024-01-01T10:00:00-06:00",
                            ai_caption="a piece of paper with writing on it"))
    assert j.is_noise(photo("2024-01-01T10:00:00-06:00", labels="document, receipt"))


def test_is_noise_keeps_animals_and_regular_photos():
    assert not j.is_noise(photo("2024-01-01T10:00:00-06:00",
                                ai_caption="a black and white cat sitting in a cardboard box"))
    assert not j.is_noise(photo("2024-01-01T10:00:00-06:00",
                                ai_caption="a group of people standing in front of a building"))
    assert not j.is_noise(photo("2024-01-01T10:00:00-06:00"))


def test_select_owner_photos_drops_noise_by_default():
    windows = [("2024-01-01", "iPhone 13 Pro")]
    clean = photo("2024-01-01T10:00:00-06:00", "iPhone 13 Pro", ai_caption="a cat on a couch")
    junk = photo("2024-01-01T11:00:00-06:00", "iPhone 13 Pro",
                 ai_caption="a close up of a barcode")
    assert [p.ai_caption for p in j.select_photos([clean, junk], windows)] == \
        ["a cat on a couch"]
    assert len(j.select_photos([clean, junk], windows, keep_noise=True)) == 2


def test_select_photos_owner_off_keeps_all_cameras():
    windows = [("2024-01-01", "iPhone 13 Pro")]
    photos = [
        photo("2024-01-01T10:00:00-06:00", "iPhone 13 Pro", ai_caption="a"),
        photo("2024-01-01T11:00:00-06:00", "Canon EOS 7D", ai_caption="b"),
        photo("2024-01-01T12:00:00-06:00", "iPhone 15 Pro", ai_caption="c"),
    ]
    on = {p.camera_model for p in j.select_photos(photos, windows, owner=True)}
    off = {p.camera_model for p in j.select_photos(photos, windows, owner=False)}
    assert on == {"iPhone 13 Pro"}
    assert off == {"iPhone 13 Pro", "Canon EOS 7D", "iPhone 15 Pro"}


def test_inherit_locations_within_window():
    windows = [("2024-01-01", "iPhone 13 Pro")]
    located = photo("2024-01-01T10:00:00-06:00", "iPhone 13 Pro",
                    latitude=32.78, longitude=-96.8,
                    search_info={"city": "Dallas", "state_abbreviation": "TX"})
    target = photo("2024-01-01T10:03:00-06:00", "iPhone 13 Pro")
    selected = j.select_photos([located, target], windows)
    assert target.inherited_location == "Dallas, TX"
    assert target.inherited_coord == (32.78, -96.8)
    assert target in selected


def test_inherit_minutes_zero_disables():
    windows = [("2024-01-01", "iPhone 13 Pro")]
    located = photo("2024-01-01T10:00:00-06:00", "iPhone 13 Pro",
                    search_info={"city": "Dallas", "state_abbreviation": "TX"})
    target = photo("2024-01-01T10:03:00-06:00", "iPhone 13 Pro")
    j.select_photos([located, target], windows, inherit_minutes=0)
    assert target.inherited_location is None


def test_inherit_locations_owner_pool_ignores_other_cameras():
    windows = [("2024-01-01", "iPhone 13 Pro")]
    other = photo("2024-01-01T10:00:00-06:00", "Canon EOS 7D", latitude=32.78,
                  longitude=-96.8,
                  search_info={"city": "Dallas", "state_abbreviation": "TX"})
    target = photo("2024-01-01T10:03:00-06:00", "iPhone 13 Pro")
    j.select_photos([other, target], windows, owner=True)
    assert target.inherited_location is None
    j.select_photos([other, target], windows, owner=False)
    assert target.inherited_location == "Dallas, TX"


def test_name_from_gps_names_gps_only_photos():
    windows = [("2024-01-01", "iPhone 13 Pro")]
    labeled = photo("2024-01-01T10:00:00-06:00", "iPhone 13 Pro",
                    latitude=32.78, longitude=-96.8,
                    search_info={"city": "Dallas", "state_abbreviation": "TX"})
    target = photo("2024-01-01T12:00:00-06:00", "iPhone 13 Pro",
                   latitude=32.7801, longitude=-96.8001)  # ~15 m away, no place
    j.select_photos([labeled, target], windows)
    assert target.inherited_location == "Dallas, TX"
    assert target.inherited_coord is None  # keeps its own coordinates


def test_name_from_gps_respects_tolerance():
    windows = [("2024-01-01", "iPhone 13 Pro")]
    labeled = photo("2024-01-01T10:00:00-06:00", "iPhone 13 Pro",
                    latitude=32.78, longitude=-96.8,
                    search_info={"city": "Dallas", "state_abbreviation": "TX"})
    far = photo("2024-01-01T10:05:00-06:00", "iPhone 13 Pro",
                latitude=32.9, longitude=-96.8)  # ~13 km away
    j.select_photos([labeled, far], windows, location_guess_km=0.1)
    assert far.inherited_location is None


def test_name_from_gps_zero_disables():
    windows = [("2024-01-01", "iPhone 13 Pro")]
    labeled = photo("2024-01-01T10:00:00-06:00", "iPhone 13 Pro",
                    latitude=32.78, longitude=-96.8,
                    search_info={"city": "Dallas", "state_abbreviation": "TX"})
    target = photo("2024-01-01T10:05:00-06:00", "iPhone 13 Pro",
                   latitude=32.7801, longitude=-96.8001)
    j.select_photos([labeled, target], windows, location_guess_km=0)
    assert target.inherited_location is None


# --- location labels ------------------------------------------------------

def test_location_label_variants():
    home = j.Home(label="Home — Fairview, TN",
                  addresses=("100 Example St",),
                  places=("Fairview",))
    assert j.location_label(
        photo("2024-01-01T10:00:00-06:00",
              place_address="100 Example St, Fairview, TN"), home) == home.label
    assert j.location_label(
        photo("2024-01-01T10:00:00-06:00",
              place_name="Fairview, Tennessee, United States"), home) == home.label
    assert j.location_label(
        photo("2024-01-01T10:00:00-06:00", search_info={
            "venues": ["Delhi6 Indian Kitchen"], "city": "Bengaluru",
            "state_abbreviation": "KA"})
    ) == "Delhi6 Indian Kitchen — Bengaluru, KA"
    assert j.location_label(
        photo("2024-01-01T10:00:00-06:00", search_info={
            "landmarks": ["Alamo Mission"], "city": "San Antonio",
            "state_abbreviation": "TX"})
    ) == "Alamo Mission — San Antonio, TX"
    assert j.location_label(
        photo("2024-01-01T10:00:00-06:00", search_info={
            "city": "Paris", "state_abbreviation": ""})
    ) == "Paris"
    assert j.location_label(photo("2024-01-01T10:00:00-06:00")) == j.UNKNOWN


def test_home_flags_default_to_plain_home():
    assert j.location_label(
        photo("2024-01-01T10:00:00-06:00", search_info={"home": "Home"})
    ) == j.DEFAULT_HOME_LABEL
    assert j.location_label(
        photo("2024-01-01T10:00:00-06:00", place_address="100 Example St")
    ) != j.DEFAULT_HOME_LABEL


def test_location_label_prefers_specific_place_name_over_town():
    label = j.location_label(photo(
        "2024-05-27T18:54:00-05:00",
        place_name="Millennium Station, Chicago, Chicagoland, United States",
        search_info={"city": "Chicago", "state_abbreviation": "IL"},
    ))
    assert label == "Millennium Station — Chicago, IL"


def test_location_label_uses_area_and_neighborhood():
    assert j.location_label(photo(
        "2024-05-28T08:00:00-05:00",
        place_name="Chicago, Chicagoland, United States, Chicago River",
        search_info={"city": "Chicago", "state_abbreviation": "IL",
                     "areas_of_interest": ["Chicago River"]},
    )) == "Chicago River"
    assert j.location_label(photo(
        "2024-01-01T10:00:00-06:00",
        search_info={"city": "Bengaluru", "state_abbreviation": "KA",
                     "neighborhoods": ["Whitefield"]},
    )) == "Whitefield — Bengaluru, KA"


def test_location_label_skips_highways():
    assert j.location_label(photo(
        "2024-01-01T10:00:00-06:00",
        place_name="I-280 S, San Jose, CA, United States",
        search_info={"city": "San Jose", "state_abbreviation": "CA",
                     "streets": ["I-280 S"]},
    )) == "San Jose, CA"
    assert j.location_label(photo(
        "2024-01-01T10:00:00-06:00",
        search_info={"city": "Chicago", "state_abbreviation": "IL",
                     "streets": ["N Riverside Plaza"]},
    )) == "N Riverside Plaza — Chicago, IL"


# --- text helpers ---------------------------------------------------------

def test_sentence_and_plural():
    assert j.sentence("a sandy beach") == "A sandy beach"
    assert j.plural(1, "photo") == "1 photo"
    assert j.plural(3, "photo") == "3 photos"
    assert j.count_note(1, video=True) == "1 video"
    assert j.count_note(2, video=True) == "2 photos/videos"
    assert j.count_note(1, video=False) == "1 photo"


def test_format_owner_phones():
    windows = [("2024-01-01", "iPhone 13 Pro"),
               ("2024-09-20", "iPhone 16 Pro"),
               ("2026-06-07", "iPhone 17 Pro Max")]
    assert j.format_owner_phones(windows) == (
        "iPhone 13 Pro → iPhone 16 Pro (Sep 20, 2024) → iPhone 17 Pro Max (Jun 7, 2026)"
    )
    assert j.format_owner_phones([]) == "unknown"


# --- clustering -----------------------------------------------------------

def test_cluster_entries_merges_similar_captions():
    entries = [
        entry("2024-01-01T10:00:00", "a group of fish swimming in a fish tank"),
        entry("2024-01-01T10:01:00", "a group of fish swimming in a tank"),
        entry("2024-01-01T11:00:00", "a plate of food on a table"),
    ]
    clusters = j.cluster_entries(entries)
    assert len(clusters) == 2
    assert len(clusters[0][1]) == 2


def test_cluster_entries_groups_reworded_captions():
    entries = [
        entry("2024-01-01T10:00:00", "a group of people standing in front of a building"),
        entry("2024-01-01T10:01:00", "people standing outside a building"),
    ]
    assert len(j.cluster_entries(entries, 0.5)) == 1


def test_cluster_entries_label_fallback_for_uncaptioned():
    entries = [
        entry("2024-01-01T10:00:00", None, labels="horse, riding, outdoor"),
        entry("2024-01-01T10:01:00", None, labels="horse, riding, outdoor"),
    ]
    clusters = j.cluster_entries(entries)
    assert len(clusters) == 1
    assert clusters[0][0] == "horse, riding, outdoor"


# --- batching -------------------------------------------------------------

def test_build_batches_splits_on_gap_and_location():
    photos = [
        photo("2024-01-01T10:00:00-06:00", search_info={"city": "Fairview"}),
        photo("2024-01-01T10:10:00-06:00", search_info={"city": "Fairview"}),
        photo("2024-01-01T14:00:00-06:00", search_info={"city": "Fairview"}),  # gap
        photo("2024-01-01T14:05:00-06:00", search_info={
            "venues": ["Some Cafe"], "city": "Dallas", "state_abbreviation": "TX"}),
    ]
    batches = j.build_batches(photos)
    assert [len(b.entries) for b in batches] == [2, 1, 1]
    assert batches[2].location == "Some Cafe — Dallas, TX"


def test_build_batches_inherits_location_for_missing_gps():
    photos = [
        photo("2024-01-01T10:00:00-06:00", search_info={
            "venues": ["Some Cafe"], "city": "Dallas", "state_abbreviation": "TX"}),
        photo("2024-01-01T10:05:00-06:00"),  # no location, within the gap
    ]
    batches = j.build_batches(photos)
    assert len(batches) == 1
    assert batches[0].location == "Some Cafe — Dallas, TX"


def test_build_batches_collects_people_and_activities():
    photos = [
        photo("2024-01-01T10:00:00-06:00", persons="Riley, _UNKNOWN_, Leo",
              search_info={"activities": ["Dining"], "holidays": ["Carnival"]}),
    ]
    batch = j.build_batches(photos)[0]
    assert batch.names == {"Riley", "Leo"}
    assert batch.activities == {"Dining"}
    assert batch.holidays == {"Carnival"}


def test_build_batches_excludes_owner_name():
    photos = [photo("2024-01-01T10:00:00-06:00", persons="Riley, Alex Doe")]
    batch = j.build_batches(photos, owner_name={"Alex Doe"})[0]
    assert batch.names == {"Riley"}


def test_build_batches_splits_on_distance_from_anchor():
    def at(lat):
        return photo("2024-01-01T10:00:00-06:00", latitude=lat, longitude=-87.0,
                     search_info={"city": "Chicago", "state_abbreviation": "IL"})

    # First photo sets the anchor; the third is >0.005° away from it.
    batches = j.build_batches([
        at(41.000), at(41.003), at(41.006),
    ])
    assert [len(b.entries) for b in batches] == [2, 1]
    assert all(b.location == "Chicago, IL" for b in batches)


def test_build_batches_distance_threshold_is_configurable():
    def at(lat):
        return photo("2024-01-01T10:00:00-06:00", latitude=lat, longitude=-87.0,
                     search_info={"city": "Chicago", "state_abbreviation": "IL"})

    photos = [at(41.000), at(41.010)]
    assert len(j.build_batches(photos)) == 2
    assert len(j.build_batches(photos, location_degrees=0.1)) == 1


# --- rendering ------------------------------------------------------------

def test_render_structure_and_grammar():
    photos = [
        photo("2024-01-01T09:56:00-06:00", ai_caption="a child sleeping on a couch",
              persons="Ava", favorite=True,
              search_info={"city": "Fairview", "home": "Home"}),
        photo("2024-01-03T19:22:00-06:00", ai_caption="a group of fish swimming",
              media_type="video"),
    ]
    windows = j.infer_owner_windows(photos)
    doc = j.render(j.build_batches(j.select_photos(photos, windows)), windows)
    assert doc.startswith("# Photo Journal")
    assert "## 2024" in doc
    assert "### January 2024" in doc
    assert "#### Monday, January 1 · iPhone 13 Pro" in doc
    assert "**Home**" in doc
    assert "*(1 photo; ★1)*" in doc
    assert "with Ava" in doc
    assert "*(1 video)*" in doc
    assert "1 photos" not in doc


def test_render_empty_has_header_only():
    doc = j.render([], [])
    assert doc.startswith("# Photo Journal")
    assert "owner photos" not in doc


# --- photo links ----------------------------------------------------------

UUID = "ABCDEF00-0000-0000-0000-000000000000"


def _entry_with_asset(uuid=UUID, filename="ABCDEF00-0000-0000-0000-000000000000.heic"):
    return entry("2024-01-01T10:00:00", caption="a cat", uuid=uuid, filename=filename)


def test_photo_url_preview_uses_jpeg_derivative(tmp_path):
    derivative = tmp_path / "resources" / "derivatives" / "A" / f"{UUID}_1_105_c.jpeg"
    derivative.parent.mkdir(parents=True)
    derivative.write_bytes(b"jpeg")
    url = j.photo_url(_entry_with_asset(), "preview", tmp_path)
    assert url == derivative.as_uri()
    assert url.startswith("file://")


def test_photo_url_original_uses_originals(tmp_path):
    original = tmp_path / "originals" / "A" / f"{UUID}.heic"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"heic")
    url = j.photo_url(_entry_with_asset(), "original", tmp_path)
    assert url == original.as_uri()


def test_photo_url_photos_mode_and_none(tmp_path):
    entry_ = _entry_with_asset()
    assert j.photo_url(entry_, "photos", tmp_path) == \
        f"photos://asset?identifier={UUID}"
    assert j.photo_url(entry_, "none", tmp_path) is None


def test_photo_url_falls_back_to_photos_app(tmp_path):
    # No files on disk -> the library path cannot resolve, so use the app scheme.
    assert j.photo_url(_entry_with_asset(), "preview", tmp_path) == \
        f"photos://asset?identifier={UUID}"
    assert j.photo_url(_entry_with_asset(), "preview", None) == \
        f"photos://asset?identifier={UUID}"


def test_photo_url_without_uuid_is_none():
    assert j.photo_url(entry("2024-01-01T10:00:00"), "preview", None) is None


def test_photo_url_thumb_returns_relative_path(tmp_path):
    thumb_dir = tmp_path / "photos"
    thumb_dir.mkdir()
    (thumb_dir / f"{UUID}.jpg").write_bytes(b"x")
    url = j.photo_url(_entry_with_asset(), "thumb", tmp_path, thumb_dir)
    assert url == f"photos/{UUID}.jpg"


def test_photo_url_thumb_falls_back_without_source(tmp_path):
    url = j.photo_url(_entry_with_asset(), "thumb", tmp_path, tmp_path / "photos")
    assert url == f"photos://asset?identifier={UUID}"


def test_materialize_thumbs_generates_files(tmp_path):
    if not shutil.which("sips"):
        pytest.skip("sips not available")
    library = tmp_path / "lib"
    original = library / "originals" / "A" / f"{UUID}.png"
    original.parent.mkdir(parents=True)
    original.write_bytes(TINY_PNG)
    thumb_dir = tmp_path / "photos"
    photos = [photo("2024-01-01T10:00:00-06:00", ai_caption="a cat", uuid=UUID,
                    filename=f"{UUID}.png", search_info={"city": "Fairview"})]
    windows = j.infer_owner_windows(photos)
    batches = j.build_batches(j.select_photos(photos, windows))
    doc = j.render(batches, windows, link="thumb", library=library, thumb_dir=thumb_dir)
    assert (thumb_dir / f"{UUID}.jpg").exists()
    assert f"[1 photo](photos/{UUID}.jpg)" in doc


def test_render_links_the_photo_count(tmp_path):
    photos = [photo("2024-01-01T10:00:00-06:00", ai_caption="a cat",
                    uuid=UUID, filename="x.heic", search_info={"city": "Fairview"})]
    windows = j.infer_owner_windows(photos)
    batches = j.build_batches(j.select_photos(photos, windows))
    doc = j.render(batches, windows, link="photos", library=tmp_path)
    assert f"[1 photo](photos://asset?identifier={UUID})" in doc
    # The link must not be nested inside emphasis/parentheses.
    assert "*([1 photo]" not in doc
    plain = j.render(batches, windows, link="none")
    assert "photos://" not in plain
    assert "*(1 photo)*" in plain


def test_render_html_links_escapes_and_structure(tmp_path):
    photos = [photo("2024-01-01T10:00:00-06:00", ai_caption="a cat & a dog <3",
                    uuid=UUID, filename="x.heic", search_info={"city": "Fairview"})]
    windows = j.infer_owner_windows(photos)
    batches = j.build_batches(j.select_photos(photos, windows))
    doc = j.render_html(batches, windows, link="photos", library=tmp_path)
    assert doc.startswith("<!DOCTYPE html>")
    assert doc.rstrip().endswith("</html>")
    assert f'<a href="photos://asset?identifier={UUID}">1 photo</a>' in doc
    assert "<h2>2024</h2>" in doc
    assert "&amp;" in doc and "&lt;3" in doc


def test_render_html_unlinked_uses_emphasis(tmp_path):
    photos = [photo("2024-01-01T10:00:00-06:00", ai_caption="a cat",
                    search_info={"city": "Fairview"})]
    windows = j.infer_owner_windows(photos)
    batches = j.build_batches(j.select_photos(photos, windows))
    doc = j.render_html(batches, windows, link="none")
    assert "<em>(1 photo)</em>" in doc
    assert "<a href=" not in doc


# --- narrative ------------------------------------------------------------

def _day_narrative_for(photos):
    batches = j.build_batches(j.select_photos(photos))
    items = [(batch, j.cluster_entries(batch.entries)) for batch in batches]
    text, _ = j.day_narrative(batches[0].start.date(), items)
    return text


def test_day_narrative_single_location():
    text = _day_narrative_for([
        photo("2024-01-01T09:00:00-06:00", ai_caption="a cat on a couch",
              search_info={"home": "Home"}),
        photo("2024-01-01T10:00:00-06:00", ai_caption="a dog on a bed",
              search_info={"home": "Home"}),
    ])
    assert text.startswith("On Monday, January 1, in the morning they were at home")


def test_day_narrative_singular_no_location():
    text = _day_narrative_for([photo("2024-01-01T10:00:00-06:00", ai_caption="a cat")])
    assert "the 1 photo has no location metadata" in text


def test_day_narrative_interleaves_where_who_what():
    text = _day_narrative_for([
        photo("2024-01-01T09:00:00-06:00", ai_caption="a cat",
              persons="Ava", search_info={"home": "Home"}),
        photo("2024-01-01T14:00:00-06:00", ai_caption="a museum",
              search_info={"venues": ["Some Museum"], "city": "Dallas",
                           "state_abbreviation": "TX"}),
    ])
    assert "with Ava" in text
    assert "Some Museum — Dallas, TX" in text
    assert "taking photos of a museum" in text


def test_day_narrative_people_caption_describes_the_named_people():
    text = _day_narrative_for([
        photo("2024-01-01T14:00:00-06:00",
              ai_caption="a group of people and children sitting at a table with bowls of food",
              persons="Riley, Ava, Emma",
              search_info={"venues": ["Some Cafe"], "city": "Dallas",
                           "state_abbreviation": "TX"}),
    ])
    # The named people are the group in the photo, not companions of it.
    assert "with Ava, Emma, and Riley, a group of people and children sitting at a table" in text
    assert "taking photos of a group of people" not in text


def test_day_narrative_trip_continuity():
    home = (33.0, -97.0)

    def narr(date, lat, lon, previous):
        photos = [photo(f"{date}T12:00:00-06:00", ai_caption="a museum",
                        latitude=lat, longitude=lon,
                        search_info={"city": "Faraway", "state_abbreviation": "IL"})]
        batches = j.build_batches(photos)
        items = [(batch, j.cluster_entries(batch.entries)) for batch in batches]
        day = j.parse_dt(f"{date}T00:00:00").date()
        return j.day_narrative(day, items, home, previous)

    text1, state1 = narr("2024-06-01", 41.0, -87.0, None)
    text2, state2 = narr("2024-06-02", 41.001, -87.0, state1)
    text3, _ = narr("2024-06-03", 30.0, -97.0, state2)
    assert "set out for" in text1
    assert "trip continued" in text2
    assert "moved on to" in text3


def test_day_narrative_scales_with_more_lines():
    small = _day_narrative_for([
        photo("2024-01-01T10:00:00-06:00", ai_caption="a cat")])
    big = _day_narrative_for([
        photo(f"2024-01-01T10:{i:02d}:00-06:00", ai_caption=f"scene number {i} unique {i}")
        for i in range(30)])
    assert len(big) > len(small)


def test_render_can_omit_narrative():
    photos = [photo("2024-01-01T09:00:00-06:00", ai_caption="a cat",
                    search_info={"home": "Home"})]
    batches = j.build_batches(j.select_photos(photos))
    assert "On Monday, January 1" in j.render(batches, narrative=True)
    assert "On Monday, January 1" not in j.render(batches, narrative=False)


def test_render_html_includes_narrative():
    photos = [photo("2024-01-01T09:00:00-06:00", ai_caption="a cat",
                    search_info={"home": "Home"})]
    batches = j.build_batches(j.select_photos(photos))
    assert '<p class="narrative">' in j.render_html(batches, narrative=True)
    assert '<p class="narrative">' not in j.render_html(batches, narrative=False)


# --- end to end -----------------------------------------------------------

def test_build_journal_selects_only_owner_photos():
    photos = [
        photo("2024-01-02T10:00:00-06:00", "iPhone 13 Pro", ai_caption="first"),
        photo("2024-01-03T10:00:00-06:00", "iPhone 13 Pro", ai_caption="second"),
        photo("2024-01-03T10:00:00-06:00", "iPhone 13 Pro", ai_caption="duplicate"),
        photo("2024-01-03T10:00:00-06:00", "iPhone 15 Pro", ai_caption="family"),
        photo("2024-01-03T10:00:00-06:00", "Canon EOS 7D", ai_caption="dslr"),
    ]
    doc = j.build_journal(photos)
    assert "**2 photos" in doc
    assert "family" not in doc and "dslr" not in doc


# --- loaders --------------------------------------------------------------

def _write_sqlite(records, path):
    writer = SqliteWriter(path)
    for record in records:
        writer.write(record)
    writer.close()


def test_read_photos_from_sqlite(tmp_path):
    records = [build_record(make_photo(ai_caption="a sandy beach"))]
    path = tmp_path / "photos.sqlite"
    _write_sqlite(records, path)
    photos = j.read_photos([path])
    assert len(photos) == 1
    assert photos[0].camera_model == "iPhone 14 Pro"
    assert photos[0].ai_caption == "a sandy beach"


def test_read_photos_from_json(tmp_path):
    record = build_record(make_photo(ai_caption="a sandy beach"))
    path = tmp_path / "photos.json"
    path.write_text(json.dumps([record]), encoding="utf-8")
    photos = j.read_photos([path])
    assert photos[0].camera_model == "iPhone 14 Pro"
    assert photos[0].ai_caption == "a sandy beach"


# --- CLI ------------------------------------------------------------------

def test_cli_writes_journal(tmp_path, capsys):
    records = [build_record(make_photo(ai_caption="a sandy beach"))]
    sqlite_path = tmp_path / "photos.sqlite"
    _write_sqlite(records, sqlite_path)
    out = tmp_path / "journal.md"
    code = build_journal.main([str(sqlite_path), "--out", str(out)])
    assert code == 0
    assert out.read_text().startswith("# Photo Journal")
    assert "iPhone 14 Pro" in capsys.readouterr().out


def test_cli_writes_html_for_html_extension(tmp_path):
    records = [build_record(make_photo(ai_caption="a sandy beach"))]
    sqlite_path = tmp_path / "photos.sqlite"
    _write_sqlite(records, sqlite_path)
    out = tmp_path / "journal.html"
    code = build_journal.main([str(sqlite_path), "--out", str(out),
                               "--link", "none", "--quiet"])
    assert code == 0
    text = out.read_text()
    assert text.startswith("<!DOCTYPE html>")
    assert text.rstrip().endswith("</html>")


def test_cli_missing_input_returns_error(tmp_path, capsys):
    code = build_journal.main([str(tmp_path / "nope.sqlite"), "--quiet"])
    assert code == 1
    assert "not found" in capsys.readouterr().err


def test_default_inputs_use_root_export(tmp_path):
    (tmp_path / "2024").mkdir()
    (tmp_path / "2024" / "photos.sqlite").write_text("")
    (tmp_path / "photos.sqlite").write_text("")
    assert build_journal.default_inputs(tmp_path) == [tmp_path / "photos.sqlite"]
    assert build_journal.default_inputs(tmp_path / "empty") == []


# --- config ---------------------------------------------------------------

def test_generate_config_writes_defaults(tmp_path):
    path = tmp_path / "config.yaml"
    assert build_journal.main(["--generate-config", str(path)]) == 0
    data = yaml.safe_load(path.read_text())
    assert data["gap-minutes"] == 75
    assert data["location-guess-km"] == 0.1
    assert data["speculative-id"] is True
    assert data["keep-junk"] is False


def test_generate_config_applies_flags(tmp_path):
    path = tmp_path / "c.yaml"
    build_journal.main(["--generate-config", str(path),
                        "--gap-minutes", "30", "--no-speculative-id"])
    data = yaml.safe_load(path.read_text())
    assert data["gap-minutes"] == 30
    assert data["speculative-id"] is False


def test_generate_config_defaults_to_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    build_journal.main(["--generate-config", "--quiet"])
    assert (tmp_path / "config.yaml").exists()


def test_generate_config_does_not_run(tmp_path, capsys):
    # No inputs exist, but generating must still succeed and not build a journal.
    code = build_journal.main(["--generate-config", str(tmp_path / "c.yaml")])
    assert code == 0
    assert "Wrote" in capsys.readouterr().out


def test_config_sets_settings(tmp_path):
    records = [build_record(make_photo(ai_caption="a cat"))]
    sqlite_path = tmp_path / "photos.sqlite"
    _write_sqlite(records, sqlite_path)
    cfg = tmp_path / "c.yaml"
    cfg.write_text(yaml.safe_dump({"speculative-id": False, "out": str(tmp_path / "j.md")}))
    assert build_journal.main([str(sqlite_path), "--config", str(cfg),
                               "--link", "none", "--quiet"]) == 0
    assert "Owner-phone detection is off" in (tmp_path / "j.md").read_text()


def test_flags_override_config(tmp_path):
    records = [build_record(make_photo(ai_caption="a cat"))]
    sqlite_path = tmp_path / "photos.sqlite"
    _write_sqlite(records, sqlite_path)
    cfg = tmp_path / "c.yaml"
    cfg.write_text(yaml.safe_dump({"speculative-id": True}))
    out = tmp_path / "j.md"
    build_journal.main([str(sqlite_path), "--config", str(cfg),
                        "--no-speculative-id", "--out", str(out),
                        "--link", "none", "--quiet"])
    assert "Owner-phone detection is off" in out.read_text()


def test_default_config_used_when_present(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    records = [build_record(make_photo(ai_caption="a cat"))]
    sqlite_path = tmp_path / "photos.sqlite"
    _write_sqlite(records, sqlite_path)
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({"speculative-id": False}))
    out = tmp_path / "j.md"
    build_journal.main([str(sqlite_path), "--out", str(out),
                        "--link", "none", "--quiet"])
    assert "Owner-phone detection is off" in out.read_text()


def test_config_missing_settings_use_defaults(tmp_path, capsys):
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump({"gap-minutes": 12}))
    cfg = build_journal.load_config(path)
    assert cfg == {"gap-minutes": 12}
    assert "unknown" not in capsys.readouterr().err


def test_config_unknown_keys_warn(tmp_path, capsys):
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump({"gap-minutes": 12, "nonsense": 1}))
    cfg = build_journal.load_config(path)
    assert cfg == {"gap-minutes": 12}
    assert "nonsense" in capsys.readouterr().err


def test_config_flag_overrides_only_specified(tmp_path):
    args = build_journal.parse_args(["--gap-minutes", "30"])
    assert build_journal.flag_overrides(args) == {"gap-minutes": 30}


def test_generate_config_includes_home_defaults(tmp_path):
    path = tmp_path / "config.yaml"
    build_journal.main(["--generate-config", str(path)])
    data = yaml.safe_load(path.read_text())
    assert data["home-label"] == "Home"
    assert data["home-address"] == []
    assert data["home-place"] == []


def test_config_home_address_is_labeled(tmp_path):
    records = [build_record(make_photo(
        ai_caption="a cat", place=make_place(address="100 Example St")))]
    sqlite_path = tmp_path / "photos.sqlite"
    _write_sqlite(records, sqlite_path)
    cfg = tmp_path / "c.yaml"
    cfg.write_text(yaml.safe_dump({"home-label": "Casa",
                                   "home-address": ["100 Example St"]}))
    out = tmp_path / "j.md"
    build_journal.main([str(sqlite_path), "--config", str(cfg),
                        "--out", str(out), "--link", "none", "--quiet"])
    assert "**Casa**" in out.read_text()


# --- vault output ---------------------------------------------------------

def summary(date, *, away=False, places=None, people=None, photos=1, videos=0,
            favorites=0, activities=None, holidays=None, timezones=None,
            cities=None, country_codes=None, route_bits=None, airport=False,
            device=None):
    return j.DaySummary(
        day=datetime.fromisoformat(date),
        device=device or ["iPhone 13 Pro"],
        away=away,
        places=places or [("Somewhere — TX", None)],
        people=people or [],
        photos=photos,
        videos=videos,
        favorites=favorites,
        activities=activities or [],
        holidays=holidays or [],
        timezones=timezones or [],
        cities=cities or [],
        country_codes=country_codes or [],
        route_bits=route_bits or [],
        airport=airport,
    )


def test_slugify_and_tz_label():
    assert j._slugify("CA Val d'Europe") == "ca-val-d-europe"
    assert j._tz_label("GMT-0600") == "UTC-06:00"
    assert j._tz_label("GMT+0530") == "UTC+05:30"
    assert j._tz_label("America/Chicago") == "America/Chicago"


def test_is_airport_detects_venue_type_and_name():
    assert j._is_airport({"venue_types": ["Airport"]})
    assert j._is_airport({"venue_types": ["Airport Terminal"]})
    assert j._is_airport({"venues": ["Dallas/Fort Worth International Airport"]})
    assert not j._is_airport({"venue_types": ["Museum"], "venues": ["The Met"]})


def test_segment_trips_groups_contiguous_away_days():
    days = [
        summary("2024-06-01T10:00:00", away=True, cities=["Chicago"],
                country_codes=["US"], route_bits=["Chicago, US"]),
        summary("2024-06-02T10:00:00", away=True, cities=["Chicago"],
                country_codes=["US"], route_bits=["Chicago, US"]),
        summary("2024-06-10T10:00:00", away=False),
        summary("2024-07-01T10:00:00", away=True, cities=["Paris"],
                country_codes=["FR"], route_bits=["Paris, FR"]),
        summary("2024-07-02T10:00:00", away=True, cities=["Paris"],
                country_codes=["FR"], route_bits=["Paris, FR"]),
        summary("2024-07-03T10:00:00", away=True, cities=["Paris"],
                country_codes=["FR"], route_bits=["Paris, FR"]),
    ]
    trips = j.segment_trips(days)
    assert len(trips) == 2
    assert trips[0].day_count == 2 and trips[0].route == "Chicago, US"
    assert trips[1].day_count == 3 and "Paris, FR" in trips[1].route
    assert "chicago-2024" in trips[0].slug


def test_segment_trips_single_day_qualifies_with_airport():
    days = [summary("2024-06-01T10:00:00", away=False, airport=True,
                    cities=["Dallas"], country_codes=["US"],
                    route_bits=["Dallas, US"])]
    assert len(j.segment_trips(days)) == 1
    # A single day that is neither away nor airport/foreign is dropped.
    assert j.segment_trips([summary("2024-06-01T10:00:00", away=False)]) == []


def test_day_index_line_has_inline_fields():
    line = j._day_index_line(
        summary("2026-04-11T10:00:00", away=True, people=["Casey"], photos=41,
                videos=6, favorites=2, timezones=["GMT+0200"],
                places=[("16th Arr. — Paris", (48.8566, 2.3522))]),
        "prague-paris-2026",
    )
    assert line.startswith("- date:: 2026-04-11")
    assert "weekday:: Saturday" in line
    assert "trip:: prague-paris-2026" in line
    assert "away:: true" in line
    assert "places:: 16th Arr. — Paris (48.8566, 2.3522)" in line
    assert "people:: Casey" in line
    assert "photos:: 41" in line and "videos:: 6" in line and "favorites:: 2" in line
    assert "tz:: UTC+02:00" in line


def test_render_vault_splits_by_year_and_lists_trips():
    photos = [
        photo("2024-06-01T10:00:00-05:00", ai_caption="a beach",
              search_info={"city": "Chicago", "state_abbreviation": "IL"},
              latitude=41.88, longitude=-87.63),
        photo("2024-06-02T10:00:00-05:00", ai_caption="a beach",
              search_info={"city": "Chicago", "state_abbreviation": "IL"},
              latitude=41.88, longitude=-87.63),
        photo("2024-08-01T10:00:00-05:00", ai_caption="a park",
              search_info={"city": "Fairview", "home": "Home"},
              latitude=33.10, longitude=-97.13),
    ]
    batches = j.build_batches(photos)
    files = j.render_vault(batches, [])
    assert set(files) == {j.Path("2024.md"), j.Path("trips.md")}
    year = files[j.Path("2024.md")]
    assert year.startswith("# Photo Journal — 2024")
    assert "## Day index" in year and "## Entries" in year
    assert "date:: 2024-06-01" in year
    assert "away:: true" in year
    trips = files[j.Path("trips.md")]
    assert "chicago-2024" in trips and "route:: Chicago" in trips


def test_render_vault_link_none_has_no_photo_links():
    photos = [photo("2024-01-01T10:00:00-06:00", ai_caption="a cat",
                    search_info={"city": "Fairview", "home": "Home"})]
    batches = j.build_batches(photos)
    files = j.render_vault(batches, [])
    text = "".join(files.values())
    assert "photos/" not in text and "photos://" not in text


def test_build_vault_defaults_to_no_links_and_no_narrative():
    photos = [photo("2024-01-01T10:00:00-06:00", ai_caption="a cat",
                    search_info={"city": "Fairview", "home": "Home"})]
    files = j.build_vault(photos)
    text = "".join(files.values())
    assert "photos/" not in text
    assert "On Monday, January 1" not in text
