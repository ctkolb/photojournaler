"""Extract a complete metadata record from an osxphotos PhotoInfo object."""

from __future__ import annotations

from typing import Any

EXIF_FIELDS = (
    "camera_make",
    "camera_model",
    "lens_model",
    "focal_length",
    "aperture",
    "shutter_speed",
    "iso",
    "exposure_bias",
    "flash_fired",
    "metering_mode",
    "white_balance",
    "codec",
    "fps",
    "duration",
    "bit_rate",
    "sample_rate",
    "track_format",
)

FLAG_FIELDS = (
    "favorite",
    "hidden",
    "isphoto",
    "ismovie",
    "live_photo",
    "slow_mo",
    "time_lapse",
    "panorama",
    "screenshot",
    "selfie",
    "portrait",
    "hdr",
    "burst",
    "spatial",
    "shared",
    "shared_library",
    "syndicated",
    "hasadjustments",
    "external_edit",
    "has_raw",
    "israw",
    "incloud",
    "iscloudasset",
    "ismissing",
    "isreference",
    "saved_to_library",
    "visible",
    "intrash",
)


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _place(photo: Any) -> dict[str, Any] | None:
    place = getattr(photo, "place", None)
    if not place:
        return None
    return {
        "name": place.name,
        "address": place.address_str,
        "country_code": place.country_code,
        "names": place.names,
    }


def _exif(photo: Any) -> dict[str, Any]:
    exif = photo.exif_info
    if exif is None:
        return {field: None for field in EXIF_FIELDS}
    return {field: getattr(exif, field, None) for field in EXIF_FIELDS}


def _albums(photo: Any) -> list[str]:
    return list(photo.albums)


def _album_folders(photo: Any) -> list[str]:
    folders: list[str] = []
    for album in photo.album_info:
        names = album.folder_names
        if not names:
            continue
        path = "/".join(names)
        if path and path not in folders:
            folders.append(path)
    return folders


def _ml(photo: Any) -> dict[str, Any] | None:
    search_info = photo.search_info
    analysis = photo.media_analysis or {}
    caption = analysis.get("image_caption") or {}
    return {
        "ai_caption": photo.ai_caption,
        "ai_caption_confidence": caption.get("imageCaptionConfidence"),
        "labels": photo.labels_normalized,
        "search_labels": search_info.labels if search_info else None,
        "search_info": search_info.asdict() if search_info else None,
    }


def _score(photo: Any) -> float | None:
    score = photo.score
    return score.overall if score is not None else None


def build_record(photo: Any, include_ml: bool = True) -> dict[str, Any]:
    """Return a nested, JSON-serializable record for a single asset."""
    record: dict[str, Any] = {
        "uuid": photo.uuid,
        "filename": photo.filename,
        "original_filename": photo.original_filename,
        "media_type": "photo" if photo.isphoto else "video",
        "original_filesize": photo.original_filesize,
        "date": _iso(photo.date),
        "date_original": _iso(photo.date_original),
        "date_added": _iso(photo.date_added),
        "date_modified": _iso(photo.date_modified),
        "tzname": photo.tzname,
        "tzoffset": photo.tzoffset,
        "width": photo.width,
        "height": photo.height,
        "original_width": photo.original_width,
        "original_height": photo.original_height,
        "orientation": photo.orientation,
        "latitude": photo.latitude,
        "longitude": photo.longitude,
        "place": _place(photo),
        "exif": _exif(photo),
        "flags": {field: getattr(photo, field, None) for field in FLAG_FIELDS},
        "keywords": photo.keywords,
        "title": photo.title,
        "description": photo.description,
        "rating": photo.rating,
        "score": _score(photo),
        "scores": photo.score.asdict() if photo.score else None,
        "albums": _albums(photo),
        "album_folders": _album_folders(photo),
        "persons": photo.persons,
        "detected_text": photo.detected_text,
    }
    record["ml"] = _ml(photo) if include_ml else None
    return record
