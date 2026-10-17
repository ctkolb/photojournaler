"""Shared fixtures and duck-typed fakes for osxphotos objects.

The fakes mirror only the attributes that ``photosmeta.extract`` reads, so the
whole pipeline can be exercised without a real Photos library.
"""

from __future__ import annotations

import datetime as dt

import pytest

from photosmeta.extract import EXIF_FIELDS, FLAG_FIELDS


class Bag:
    """Attribute bag used to stand in for osxphotos value objects."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def make_exif(**overrides):
    values = {field: None for field in EXIF_FIELDS}
    values.update(overrides)
    return Bag(**values)


class FakeSearchInfo:
    def __init__(self, labels=None, data=None):
        self.labels = list(labels or [])
        self._data = dict(data or {})

    def asdict(self):
        return dict(self._data, labels=list(self.labels))


class FakeScore:
    def __init__(self, overall=0.5, data=None):
        self.overall = overall
        self._data = dict(data or {"overall": overall})

    def asdict(self):
        return dict(self._data)


def make_place(name="Somewhere, TX", address="1 Main St",
               country_code="US", names=None):
    return Bag(name=name, address_str=address, country_code=country_code,
               names=names if names is not None else [[]])


def make_album(title="Album", folder_names=None):
    return Bag(title=title, folder_names=list(folder_names or []))


def make_photo(**overrides):
    """Return a fake PhotoInfo with sensible defaults for every used field."""
    photo = Bag(
        uuid="00000000-0000-0000-0000-000000000000",
        filename="IMG_0001.HEIC",
        original_filename="IMG_0001.HEIC",
        original_filesize=1234567,
        date=dt.datetime(2023, 5, 4, 10, 30, 0, tzinfo=dt.timezone(dt.timedelta(hours=-5))),
        date_original=dt.datetime(2023, 5, 4, 10, 30, 0, tzinfo=dt.timezone(dt.timedelta(hours=-5))),
        date_added=dt.datetime(2023, 5, 5, 8, 0, 0, tzinfo=dt.timezone.utc),
        date_modified=dt.datetime(2023, 5, 5, 8, 0, 0, tzinfo=dt.timezone.utc),
        tzname="GMT-0500",
        tzoffset=-18000,
        width=4032,
        height=3024,
        original_width=4032,
        original_height=3024,
        orientation=1,
        latitude=33.07,
        longitude=-97.11,
        place=None,
        exif_info=make_exif(camera_make="Apple", camera_model="iPhone 14 Pro"),
        keywords=[],
        title=None,
        description=None,
        rating=0,
        score=FakeScore(),
        ai_caption=None,
        albums=[],
        album_info=[],
        persons=[],
        detected_text=[],
        labels_normalized=[],
        search_info=None,
        media_analysis=None,
    )
    for field in FLAG_FIELDS:
        setattr(photo, field, False)
    photo.isphoto = True
    photo.ismovie = False
    for key, value in overrides.items():
        setattr(photo, key, value)
    return photo


class FakeDB:
    """Stand-in for osxphotos.PhotosDB capturing constructor/query arguments."""

    def __init__(self, photos, init_kwargs=None, query_kwargs=None):
        self._photos = list(photos)
        self.init_kwargs = init_kwargs if init_kwargs is not None else {}
        self.query_kwargs = query_kwargs if query_kwargs is not None else {}

    def photos(self, **kwargs):
        self.query_kwargs.update(kwargs)
        return list(self._photos)


@pytest.fixture
def sample_photos():
    """A small, varied set of fake photos covering the interesting branches."""
    from datetime import timezone, timedelta

    cst = timezone(timedelta(hours=-6))
    photos = [
        make_photo(
            uuid="11111111-1111-1111-1111-111111111111",
            filename="beach.HEIC",
            place=make_place(name="Beach, FL", country_code="US"),
            exif_info=make_exif(camera_make="Apple", camera_model="iPhone 14 Pro",
                                lens_model="back camera", iso=64, aperture=1.78),
            keywords=["summer", "beach"],
            albums=["Vacation", "Favorites"],
            album_info=[make_album("Vacation", ["Trips", "2023"]),
                        make_album("Favorites", ["Trips"])],
            persons=["Alice", "_UNKNOWN_", "Bob"],
            labels_normalized=["people", "beach"],
            ai_caption="a sandy beach",
            search_info=FakeSearchInfo(
                labels=["Beach", "People"],
                data={"country": "United States", "labels": ["Beach", "People"]},
            ),
            media_analysis={"image_caption": {"imageCaptionText": "a sandy beach",
                                              "imageCaptionConfidence": 0.9}},
            favorite=True,
            isphoto=True,
        ),
        make_photo(
            uuid="22222222-2222-2222-2222-222222222222",
            filename="night.MOV",
            media_type=None,
            date_original=dt.datetime(2024, 1, 1, 23, 30, tzinfo=cst),
            date=dt.datetime(2024, 1, 1, 23, 30, tzinfo=cst),
            place=None,
            exif_info=make_exif(camera_make="Apple", camera_model="iPhone 15 Pro",
                                codec="hvc1", fps=30.0, duration=12.5),
            persons=[],
            isphoto=False,
            ismovie=True,
        ),
        make_photo(
            uuid="33333333-3333-3333-3333-333333333333",
            filename="plain.HEIC",
            place=make_place(name="Paris, FR", country_code="FR"),
            exif_info=make_exif(camera_make="Canon", camera_model="Canon EOS 7D",
                                focal_length=50.0, aperture=2.8),
            labels_normalized=["outdoor"],
            search_info=FakeSearchInfo(labels=["Outdoor"], data={"labels": ["Outdoor"]}),
        ),
    ]
    return photos


@pytest.fixture
def records(sample_photos):
    from photosmeta import build_record

    return [build_record(p) for p in sample_photos]


@pytest.fixture
def record(records):
    return records[0]
