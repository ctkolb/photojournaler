"""Build a markdown photo journal from exported Photos metadata.

The pipeline is deterministic and split into small, testable steps:

1. :func:`infer_owner_windows` speculates which phone belongs to the library
   owner by taking the most common iPhone per month and requiring the owner's
   model to only ever get newer (higher generation, then higher tier).
2. :func:`select_owner_photos` keeps only camera photos from the owner's phone
   (dropping screenshots, screen recordings, saved/shared images, other
   people's phones, and DSLRs) and removes duplicate album exports.
3. :func:`build_batches` groups nearby photos at the same place.
4. :func:`render` writes the journal.
"""

from __future__ import annotations

import html
import json
import re
import sqlite3
import subprocess
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable

HOME_ADDR = "100 Example St"
HOME_LABEL = "Home — Fairview, TX"
UNKNOWN = "No location metadata"
DEFAULT_GAP_MINUTES = 75
DEFAULT_SIMILARITY = 0.5
DEFAULT_LOCATION_DEGREES = 0.005  # ~0.5 km; start a new place once you move this far
DEFAULT_LIBRARY = Path.home() / "Pictures" / "Photos Library.photoslibrary"
DEFAULT_THUMB_SIZE = 400
LINK_MODES = ("thumb", "preview", "original", "photos", "none")

_TIERS = (("Pro Max", 3), ("Pro", 2), ("Plus", 1))
_MODEL_RE = re.compile(r"^iPhone\s+(\d+)\b")


@dataclass
class Photo:
    """A single exported asset, reduced to the fields the journal needs."""

    date_original: str
    camera_model: str | None = None
    screenshot: bool = False
    place_name: str | None = None
    place_address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    ai_caption: str | None = None
    labels: str | None = None
    persons: str | None = None
    favorite: bool = False
    media_type: str | None = None
    uuid: str | None = None
    filename: str | None = None
    search_info: dict = field(default_factory=dict)

    @property
    def dt(self) -> datetime | None:
        return parse_dt(self.date_original)

    @property
    def coord(self) -> tuple[float, float] | None:
        if self.latitude is None or self.longitude is None:
            return None
        return (self.latitude, self.longitude)

    @classmethod
    def from_row(cls, row) -> "Photo":
        search_info = row["search_info"]
        return cls(
            date_original=row["date_original"],
            camera_model=row["camera_model"],
            screenshot=_truthy(row["screenshot"]),
            place_name=row["place_name"],
            place_address=row["place_address"],
            latitude=row["latitude"],
            longitude=row["longitude"],
            ai_caption=row["ai_caption"],
            labels=row["labels"],
            persons=row["persons"],
            favorite=_truthy(row["favorite"]),
            media_type=row["media_type"],
            uuid=row["uuid"],
            filename=row["filename"],
            search_info=json.loads(search_info) if search_info else {},
        )

    @classmethod
    def from_record(cls, record: dict) -> "Photo":
        place = record.get("place") or {}
        exif = record.get("exif") or {}
        flags = record.get("flags") or {}
        ml = record.get("ml") or {}
        return cls(
            date_original=record.get("date_original") or "",
            camera_model=exif.get("camera_model"),
            screenshot=_truthy(flags.get("screenshot")),
            place_name=place.get("name"),
            place_address=place.get("address"),
            latitude=record.get("latitude"),
            longitude=record.get("longitude"),
            ai_caption=ml.get("ai_caption"),
            labels=", ".join(ml.get("labels") or []) or None,
            persons=", ".join(record.get("persons") or []) or None,
            favorite=_truthy(flags.get("favorite")),
            media_type=record.get("media_type"),
            uuid=record.get("uuid"),
            filename=record.get("filename"),
            search_info=ml.get("search_info") or {},
        )


@dataclass
class Entry:
    dt: datetime
    caption: str | None
    labels: str | None
    favorite: bool
    video: bool
    camera: str | None
    uuid: str | None = None
    filename: str | None = None
    latitude: float | None = None
    longitude: float | None = None

    @property
    def coord(self) -> tuple[float, float] | None:
        if self.latitude is None or self.longitude is None:
            return None
        return (self.latitude, self.longitude)


@dataclass
class Batch:
    location: str
    entries: list[Entry]
    names: set[str] = field(default_factory=set)
    activities: set[str] = field(default_factory=set)
    holidays: set[str] = field(default_factory=set)

    @property
    def start(self) -> datetime:
        return self.entries[0].dt

    @property
    def end(self) -> datetime:
        return self.entries[-1].dt


def _truthy(value) -> bool:
    return str(value) in ("1", "True", "true")


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def model_rank(model: str | None) -> tuple[int, int] | None:
    """Return ``(generation, tier)`` for a numeric iPhone, else ``None``."""
    if not model:
        return None
    match = _MODEL_RE.match(model)
    if not match:
        return None
    tier = next((value for name, value in _TIERS if name in model), 0)
    return (int(match.group(1)), tier)


def infer_owner_windows(photos: Iterable[Photo]) -> list[tuple[str, str]]:
    """Speculate the owner's phones as ``(first_seen_date, model)`` windows.

    Each month, the most common iPhone is the owner's (ties favor the newer
    model). The owner's model only ever moves to a newer generation/tier, and a
    window starts on the first date that model appears.
    """
    by_month: dict[tuple[int, int], Counter] = {}
    for photo in photos:
        rank = model_rank(photo.camera_model)
        dt = photo.dt
        if rank is None or dt is None:
            continue
        by_month.setdefault((dt.year, dt.month), Counter())[photo.camera_model] += 1

    sequence: list[str] = []
    owner_rank: tuple[int, int] | None = None
    for month in sorted(by_month):
        counts = by_month[month]
        top = max(counts, key=lambda model: (counts[model], model_rank(model)))
        rank = model_rank(top)
        if owner_rank is None or rank > owner_rank:
            owner_rank = rank
            if top not in sequence:
                sequence.append(top)

    windows: list[tuple[str, str]] = []
    for model in sequence:
        dates = [p.date_original for p in photos if p.camera_model == model]
        if dates:
            windows.append((min(dates), model))
    windows.sort()
    return windows


def owner_model(date_original: str, windows: list[tuple[str, str]]) -> str | None:
    model = None
    for start, candidate in windows:
        if date_original >= start:
            model = candidate
        else:
            break
    return model


def is_camera_photo(photo: Photo) -> bool:
    if photo.screenshot:
        return False
    media = photo.search_info.get("media_types") or []
    if any(m in media for m in ("Screenshots", "Screen Recordings", "Animated")):
        return False
    if photo.search_info.get("source"):
        return False
    return True


def select_owner_photos(
    photos: Iterable[Photo],
    windows: list[tuple[str, str]] | None = None,
    keep_noise: bool = False,
) -> list[Photo]:
    photos = list(photos)
    if windows is None:
        windows = infer_owner_windows(photos)
    selected = [
        photo
        for photo in photos
        if model_rank(photo.camera_model) is not None
        and photo.camera_model == owner_model(photo.date_original, windows)
        and is_camera_photo(photo)
        and (keep_noise or not is_noise(photo))
    ]
    seen: set[tuple[str, str | None]] = set()
    deduped: list[Photo] = []
    for photo in sorted(selected, key=lambda p: p.date_original):
        key = (photo.date_original, photo.camera_model)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(photo)
    return deduped


def _with_town(name: str, town: str, city: str) -> str:
    if town and city and city.lower() not in name.lower():
        return f"{name} — {town}"
    return name


_ROAD = re.compile(
    r"\b(fwy|hwy|freeway|expressway|tollway|turnpike|interstate|access rd|"
    r"i-\d+|us-\d+|sh-?\d+|rm-?\d+|fm-?\d+|loop \d+|state hwy)\b",
    re.I,
)


def location_label(photo: Photo) -> str:
    """Resolve a colloquial, as-specific-as-possible place for a photo.

    Prefers named venues, landmarks, areas of interest, bodies of water, and
    neighborhoods from Apple's reverse-geocoded place data, then the leading
    component of the place name (e.g. "Millennium Station"), and only then
    falls back to the town.
    """
    address = photo.place_address or ""
    place = photo.place_name or ""
    if address.startswith(HOME_ADDR) or photo.search_info.get("home") == "Home":
        return HOME_LABEL
    if "Riverside" in place or "Fairview" in place:
        return HOME_LABEL
    city = photo.search_info.get("city") or ""
    state = photo.search_info.get("state_abbreviation") or ""
    town = f"{city}, {state}" if city and state else (city or place)
    for field in ("venues", "landmarks", "areas_of_interest", "bodies_of_water",
                  "neighborhoods"):
        values = photo.search_info.get(field) or []
        if values:
            return _with_town(values[0], town, city)
    specific = place.split(",")[0].strip()
    if specific and city and specific.lower() != city.lower() and not _ROAD.search(specific):
        return _with_town(specific, town, city)
    streets = photo.search_info.get("streets") or []
    if streets and not _ROAD.search(streets[0]):
        return _with_town(streets[0], town, city)
    return town or UNKNOWN


def _distance_degrees(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Euclidean distance in degrees; good enough to detect a ~0.5 km move."""
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def normalize(text: str) -> str:
    return " ".join(text.lower().strip().rstrip(".").split())


_STOPWORDS = frozenset(
    "a an the of on in with and is are to at for by their it its front next "
    "his her into out up as that this from over under behind near".split()
)


def content_words(text: str) -> str:
    """Caption text with stopwords removed, so reworded captions still match."""
    words = re.findall(r"[a-z0-9']+", text.lower())
    return " ".join(w for w in words if w not in _STOPWORDS)


_NOISE_LABELS = frozenset({"barcode", "receipt", "qr code"})
_NOISE_STRONG = re.compile(
    r"barcode|qr code|receipt|printed page|business card|price tag|"
    r"shipping label|\bmenu\b|\bpaper with\b|\bpiece of paper\b|\bwhite paper\b|"
    r"\bwhite sticker\b",
    re.I,
)
_NOISE_WEAK = re.compile(
    r"a close up of (?:a |an |the )?(?:tag|label|sticker|card|box|package|"
    r"envelope|barcode|qr code)\b|cardboard box|\ba (?:package|envelope)\b|"
    r"\bpackage with\b",
    re.I,
)
_ANIMAL_WORDS = re.compile(
    r"\b(cat|dog|kitten|puppy|animal|bird|parrot|horse|cow|goat|sheep|pig|"
    r"rabbit|pet|squirrel|deer|bear|lion|tiger|zebra|giraffe|monkey|duck|"
    r"chicken|turtle|fish|dolphin|whale|elephant)\b",
    re.I,
)


def is_noise(photo: Photo) -> bool:
    """True for low-value shots: documents, barcodes, receipts, menus, boxes."""
    labels = {label.strip().lower() for label in (photo.labels or "").split(",") if label.strip()}
    if labels & _NOISE_LABELS:
        return True
    caption = photo.ai_caption or ""
    if not caption:
        return False
    if _NOISE_STRONG.search(caption):
        return True
    return bool(_NOISE_WEAK.search(caption) and not _ANIMAL_WORDS.search(caption))


def sentence(text: str) -> str:
    text = text.strip()
    return text[:1].upper() + text[1:] if text else text


def plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def count_note(count: int, video: bool) -> str:
    if video and count == 1:
        return "1 video"
    if video:
        return f"{count} photos/videos"
    return plural(count, "photo")


def _names(photo: Photo, exclude: set[str] | None = None) -> set[str]:
    people = {p for p in (photo.persons or "").split(", ") if p and p != "_UNKNOWN_"}
    people.update(photo.search_info.get("pets") or [])
    if exclude:
        people -= exclude
    return people


def _entry(photo: Photo, dt: datetime) -> Entry:
    return Entry(
        dt=dt,
        caption=photo.ai_caption or None,
        labels=photo.labels or None,
        favorite=photo.favorite,
        video=photo.media_type == "video",
        camera=photo.camera_model,
        uuid=photo.uuid,
        filename=photo.filename,
        latitude=photo.latitude,
        longitude=photo.longitude,
    )


def build_batches(
    photos: Iterable[Photo],
    gap_minutes: int = DEFAULT_GAP_MINUTES,
    location_degrees: float = DEFAULT_LOCATION_DEGREES,
    owner_name: set[str] | None = None,
) -> list[Batch]:
    """Group consecutive photos at the same place.

    A new batch starts when the idle gap exceeds ``gap_minutes``, the resolved
    place changes, or the camera has moved more than ``location_degrees`` from
    where the batch began (about 0.5 km by default). ``owner_name`` (people to
    omit, e.g. the journal's owner) is filtered out of the per-batch names.
    """
    batches: list[Batch] = []
    current: Batch | None = None
    anchor: tuple[float, float] | None = None
    for photo in photos:
        dt = photo.dt
        if dt is None:
            continue
        loc = location_label(photo)
        coord = photo.coord
        if current is not None:
            gap = (dt - current.end).total_seconds() / 60
            effective = current.location if (loc == UNKNOWN and gap <= gap_minutes) else loc
            moved = (
                coord is not None
                and anchor is not None
                and _distance_degrees(coord, anchor) > location_degrees
            )
            if gap <= gap_minutes and effective == current.location and not moved:
                current.entries.append(_entry(photo, dt))
                current.names.update(_names(photo, owner_name))
                current.activities.update(photo.search_info.get("activities") or [])
                current.holidays.update(photo.search_info.get("holidays") or [])
                if anchor is None:
                    anchor = coord
                continue
        current = Batch(
            location=loc,
            entries=[_entry(photo, dt)],
            names=_names(photo, owner_name),
            activities=set(photo.search_info.get("activities") or []),
            holidays=set(photo.search_info.get("holidays") or []),
        )
        anchor = coord
        batches.append(current)
    return batches


def cluster_entries(
    entries: list[Entry], similarity: float = DEFAULT_SIMILARITY
) -> list[tuple[str | None, list[Entry]]]:
    clusters: list[dict] = []
    uncaptioned: list[Entry] = []
    for entry in entries:
        if not entry.caption:
            uncaptioned.append(entry)
            continue
        key = content_words(entry.caption)
        for cluster in clusters:
            if any(
                SequenceMatcher(None, key, other).ratio() >= similarity
                for other in cluster["keys"]
            ):
                cluster["keys"].append(key)
                cluster["raw"][entry.caption] += 1
                cluster["entries"].append(entry)
                break
        else:
            clusters.append(
                {"keys": [key], "raw": Counter({entry.caption: 1}), "entries": [entry]}
            )
    result = [(c["raw"].most_common(1)[0][0], c["entries"]) for c in clusters]
    if uncaptioned:
        labels = Counter(e.labels for e in uncaptioned if e.labels)
        text = None
        if labels:
            text = ", ".join(labels.most_common(1)[0][0].split(", ")[:3])
        result.append((text, uncaptioned))
    result.sort(key=lambda item: min(e.dt for e in item[1]))
    return result


def _fmt_time(dt: datetime) -> str:
    return dt.strftime("%-I:%M %p")


def _fmt_range(entries: list[Entry]) -> str:
    start, end = entries[0].dt, entries[-1].dt
    if _fmt_time(start) == _fmt_time(end):
        return _fmt_time(start)
    return f"{_fmt_time(start)}–{_fmt_time(end)}"


def _tag_suffix(batch: Batch) -> str:
    tags = sorted(batch.activities) + sorted(batch.holidays)
    if batch.names:
        tags.append("with " + ", ".join(sorted(batch.names)))
    return " · ".join(dict.fromkeys(tags))


def format_owner_phones(windows: list[tuple[str, str]]) -> str:
    if not windows:
        return "unknown"
    parts = []
    for index, (start, model) in enumerate(windows):
        if index == 0:
            parts.append(model)
            continue
        dt = parse_dt(start)
        stamp = f" ({dt.strftime('%b %-d, %Y')})" if dt else ""
        parts.append(f"{model}{stamp}")
    return " → ".join(parts)


def _originals_path(library: Path, filename: str | None) -> Path | None:
    if not filename:
        return None
    candidate = library / "originals" / filename[0].upper() / filename
    return candidate if candidate.exists() else None


def _preview_path(library: Path, uuid: str | None) -> Path | None:
    if not uuid:
        return None
    bucket = uuid[0].upper()
    base = library / "resources" / "derivatives"
    candidates: list[Path] = []
    for folder in (base / bucket, base / "masters" / bucket):
        if folder.is_dir():
            candidates.extend(folder.glob(f"{uuid}_*.jpeg"))
    if not candidates:
        return None
    preferred = [p for p in candidates if p.name.endswith("_1_105_c.jpeg")]
    return sorted(preferred or candidates)[0]


def _ensure_thumb(src: Path, dest: Path, size: int) -> bool:
    """Downscale ``src`` to a small JPEG at ``dest`` using macOS ``sips``."""
    if dest.exists():
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            ["sips", "-Z", str(size), "-s", "format", "jpeg",
             "-s", "formatOptions", "60", "--out", str(dest), str(src)],
            capture_output=True,
        )
    except OSError:
        return False
    return result.returncode == 0 and dest.exists()


def _materialize_thumbs(
    entries: Iterable[Entry], library: Path | None, thumb_dir: Path | None, size: int
) -> int:
    """Copy a small preview for every example photo into ``thumb_dir``."""
    if library is None or thumb_dir is None:
        return 0
    library, thumb_dir = Path(library), Path(thumb_dir)
    seen: dict[str, Entry] = {}
    for entry in entries:
        if entry.uuid and entry.uuid not in seen:
            seen[entry.uuid] = entry
    jobs: list[tuple[Path, Path]] = []
    for uuid, entry in seen.items():
        dest = thumb_dir / f"{uuid}.jpg"
        if dest.exists():
            continue
        src = _preview_path(library, uuid) or _originals_path(library, entry.filename)
        if src:
            jobs.append((src, dest))
    if not jobs:
        return 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = pool.map(lambda job: _ensure_thumb(job[0], job[1], size), jobs)
        return sum(bool(result) for result in results)


def photo_url(
    entry: Entry,
    mode: str = "thumb",
    library: Path | None = None,
    thumb_dir: Path | None = None,
    thumb_size: int = DEFAULT_THUMB_SIZE,
) -> str | None:
    """A URL that opens an example photo, in a browser or in the Photos app.

    ``thumb`` copies a small JPEG preview next to the journal and links it
    relatively (works in any viewer), ``preview``/``original`` link the file
    inside the library with ``file://`` (may be blocked by the browser),
    ``photos`` uses the Photos app URL scheme, and ``none`` returns no link.
    Anything that cannot be resolved falls back to the ``photos`` scheme.
    """
    if mode == "none" or not entry.uuid:
        return None
    photos_url = f"photos://asset?identifier={entry.uuid}"
    if mode == "photos" or library is None:
        return photos_url
    library = Path(library)
    if mode == "thumb":
        if thumb_dir is None:
            return photos_url
        dest = Path(thumb_dir) / f"{entry.uuid}.jpg"
        if not dest.exists():
            src = _preview_path(library, entry.uuid) or _originals_path(library, entry.filename)
            if not src or not _ensure_thumb(src, dest, thumb_size):
                return photos_url
        return f"{Path(thumb_dir).name}/{dest.name}"
    if mode == "preview":
        path = _preview_path(library, entry.uuid) or _originals_path(library, entry.filename)
    elif mode == "original":
        path = _originals_path(library, entry.filename)
    else:
        path = None
    return path.as_uri() if path else photos_url


def _part_of_day(dt: datetime) -> str:
    hour = dt.hour
    if 5 <= hour < 12:
        return "in the morning"
    if 12 <= hour < 17:
        return "in the afternoon"
    if 17 <= hour < 21:
        return "in the evening"
    return "at night"


def _and_list(items: list[str]) -> str:
    items = list(dict.fromkeys(items))
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _day_groups(grouped: list[tuple[Batch, list]]) -> list[tuple]:
    days: list[tuple] = []
    for batch, clusters in grouped:
        day = batch.start.date()
        if not days or days[-1][0] != day:
            days.append((day, []))
        days[-1][1].append((batch, clusters))
    return days


TRIP_DEGREES = 0.5       # ~55 km from home counts as a trip
CONTINUE_DEGREES = 1.0   # next day within ~110 km continues the same trip


@dataclass
class TripState:
    away: bool = False
    coord: tuple[float, float] | None = None


def _median_coord(entries: list[Entry]) -> tuple[float, float] | None:
    points = [entry.coord for entry in entries if entry.coord]
    if not points:
        return None
    lats = sorted(p[0] for p in points)
    lons = sorted(p[1] for p in points)
    return (lats[len(lats) // 2], lons[len(lons) // 2])


def home_coord(batches: Iterable[Batch]) -> tuple[float, float] | None:
    """Median coordinate of photos taken at home, used to gauge trip distance."""
    lats: list[float] = []
    lons: list[float] = []
    for batch in batches:
        if batch.location != HOME_LABEL:
            continue
        for entry in batch.entries:
            if entry.coord:
                lats.append(entry.coord[0])
                lons.append(entry.coord[1])
    if not lats:
        return None
    lats.sort()
    lons.sort()
    return (lats[len(lats) // 2], lons[len(lons) // 2])


def _segments(items: list[tuple[Batch, list]]) -> list[dict]:
    segments: list[dict] = []
    for batch, clusters in items:
        if batch.location == UNKNOWN:
            continue
        coord = _median_coord(batch.entries)
        if segments and segments[-1]["location"] == batch.location:
            segment = segments[-1]
            segment["end"] = batch.end
            segment["items"].append((batch, clusters))
            if segment["coord"] is None:
                segment["coord"] = coord
        else:
            segments.append({
                "location": batch.location,
                "start": batch.start,
                "end": batch.end,
                "coord": coord,
                "items": [(batch, clusters)],
            })
    return segments


def _caption_phrase(caption: str) -> str:
    """Turn a caption into an appositive phrase: 'X is doing Y' -> 'X, doing Y'."""
    return re.sub(r"\b(?:is|are|was|were)\b", ",", caption).replace(" ,", ",")


_PEOPLE_CAPTION = re.compile(
    r"\b(people|children|kids|men|women|family|families|friends|crowd|"
    r"boys|girls)\b",
    re.I,
)


def _segment_caption(segment: dict) -> str | None:
    captions = [(len(group), caption) for _, clusters in segment["items"]
                for caption, group in clusters if caption]
    if not captions:
        return None
    captions.sort(reverse=True)
    return _caption_phrase(captions[0][1])


def _segment_activity(segment: dict) -> str | None:
    activities = sorted({a.lower() for batch, _ in segment["items"]
                         for a in batch.activities})
    return _and_list(activities) if activities else None


def day_narrative(day, items: list[tuple[Batch, list]], home=None, previous=None):
    """A short, factual story of one day, grounded in the day's metadata.

    Interleaves where they were, who was there, and what they did, in order.
    Days far from home are told at greater length, and consecutive days at a
    similar far-off place read as one continuing trip. Returns ``(text, state)``
    where ``state`` is passed back as ``previous`` for the following day.
    """
    n_photos = sum(len(batch.entries) for batch, _ in items)
    n_lines = sum(len(clusters) for _, clusters in items)
    segments = _segments(items)

    anchor = None
    for segment in segments:
        distance = 0.0
        if segment["coord"] and home:
            distance = _distance_degrees(segment["coord"], home)
        if anchor is None or distance > anchor[0]:
            anchor = (distance, segment["coord"], segment["location"])
    distance = anchor[0] if anchor else 0.0
    place = anchor[2] if anchor else None
    away = bool(home and anchor and anchor[1] and distance > TRIP_DEGREES)

    continuing = moved_on = returned = False
    if away:
        if previous and previous.away and previous.coord and anchor[1]:
            if _distance_degrees(anchor[1], previous.coord) <= CONTINUE_DEGREES:
                continuing = True
            else:
                moved_on = True
    elif previous and previous.away:
        returned = True
    state = TripState(away=away, coord=anchor[1] if away else None)

    detail = 3 if away else 2 if distance > 0.05 else 1
    if n_lines > 10:
        detail = min(detail + 1, 4)
    if n_lines > 25:
        detail = 4
    max_stops = {1: 2, 2: 4, 3: 6, 4: 9}.get(detail, 6)

    date = day.strftime("%A, %B %-d")
    sentences: list[str] = []
    if away and continuing:
        sentences.append(f"On {date}, the trip continued around {place}.")
    elif away and moved_on:
        sentences.append(f"On {date}, they moved on to {place}.")
    elif away:
        sentences.append(f"On {date}, they set out for {place}.")
    elif returned:
        sentences.append(f"On {date}, they were back home.")

    previous_time = None
    for index, segment in enumerate(segments[:max_stops]):
        time = _part_of_day(segment["start"])
        names = sorted({name for batch, _ in segment["items"] for name in batch.names})
        where = "home" if segment["location"] == HOME_LABEL else segment["location"]
        caption = _segment_caption(segment)
        activity = _segment_activity(segment)
        clause = f"{time} they were at {where}"
        if names:
            clause += f" with {_and_list(names)}"
            # The named people are the ones in the photo, so let the caption
            # describe them rather than sounding like a separate group.
            if caption and _PEOPLE_CAPTION.search(caption):
                clause += f", {caption}"
            elif caption:
                clause += f", taking photos of {caption}"
            elif activity:
                clause += f", for {activity}"
        elif caption:
            clause += f", taking photos of {caption}"
        elif activity:
            clause += f", for {activity}"
        if index == 0 and not sentences:
            sentence = f"On {date}, {clause}"
        elif time == previous_time:
            sentence = f"Later {clause}"
        else:
            sentence = clause
        sentences.append(sentence[0].upper() + sentence[1:] + ".")
        previous_time = time

    if not segments:
        noun = "photo" if n_photos == 1 else "photos"
        sentences.append(f"On {date}, the {n_photos} {noun} have no location metadata.")

    holidays = sorted({h for batch, _ in items for h in batch.holidays})
    activities = sorted({a.lower() for batch, _ in items for a in batch.activities})
    if any(meal in activities for meal in ("breakfast", "lunch", "dinner")):
        activities = [a for a in activities if a != "dining"]
    if detail >= 2 and holidays:
        sentences.append(f"It was {_and_list(holidays)}.")

    if detail >= 3:
        captions = [(len(group), caption) for _, clusters in items
                    for caption, group in clusters if caption]
        captions.sort(reverse=True)
        themes: list[str] = []
        seen: set[str] = set()
        for _, caption in captions:
            key = content_words(caption)
            if key in seen:
                continue
            seen.add(key)
            phrase = _caption_phrase(caption)
            themes.append(phrase[0].lower() + phrase[1:])
            if len(themes) == 2:
                break
        if themes:
            sentences.append("The day's photos are mostly of " + _and_list(themes) + ".")

    if detail >= 3:
        favorites = sum(1 for batch, _ in items for e in batch.entries if e.favorite)
        if favorites:
            label = "favorite stands" if favorites == 1 else "favorites stand"
            sentences.append(f"{favorites} {label} out.")

    return " ".join(sentences), state


def render(batches: list[Batch], windows: list[tuple[str, str]] | None = None,
           similarity: float = DEFAULT_SIMILARITY, link: str = "thumb",
           library: Path | None = None, thumb_dir: Path | None = None,
           thumb_size: int = DEFAULT_THUMB_SIZE, narrative: bool = True) -> str:
    grouped = [(batch, cluster_entries(batch.entries, similarity)) for batch in batches]
    if link == "thumb":
        examples = (group[0] for _, clusters in grouped for _, group in clusters)
        _materialize_thumbs(examples, library, thumb_dir, thumb_size)
    total = sum(len(batch.entries) for batch in batches)
    lines = [
        "# Photo Journal — Apple Photos",
        "",
    ]
    if batches:
        lines += [
            f"**{total:,} owner photos · {batches[0].start:%b %-d, %Y} – "
            f"{batches[-1].end:%b %-d, %Y}**",
            "",
        ]
    lines += [
        "The owner's camera photos only, in capture order.",
        "",
        f"Owner's phone (inferred from the dominant-iPhone progression): "
        f"{format_owner_phones(windows or [])}.",
        "",
        "Screenshots, screen recordings, saved/shared images, other people's "
        "phones, and DSLRs are excluded. Locations are colloquial names from "
        "reverse-geocoded place data; descriptions come from Apple's on-device "
        "ML captions, labels, and OCR. **★** marks a favorite.",
        "",
    ]

    day_cameras: dict = {}
    for batch in batches:
        cameras = day_cameras.setdefault(batch.start.date(), [])
        for camera in dict.fromkeys(entry.camera for entry in batch.entries):
            if camera not in cameras:
                cameras.append(camera)

    current_year = current_month = None
    home = home_coord(batches)
    trip = TripState()
    for day, items in _day_groups(grouped):
        if day.year != current_year:
            current_year = day.year
            current_month = None
            lines += [f"## {current_year}", ""]
        if (day.year, day.month) != current_month:
            current_month = (day.year, day.month)
            lines += [f"### {day.strftime('%B %Y')}", ""]
        lines += [f"#### {day.strftime('%A, %B %-d')} · {', '.join(day_cameras[day])}", ""]
        if narrative:
            text, trip = day_narrative(day, items, home, trip)
            lines += [text, ""]

        current_location = None
        for batch, clusters in items:
            if batch.location != current_location:
                current_location = batch.location
                lines += [f"**{current_location}**"]
            tags = _tag_suffix(batch)
            for index, (caption, group) in enumerate(clusters):
                count = len(group)
                favorites = sum(entry.favorite for entry in group)
                video = any(entry.video for entry in group)
                label = count_note(count, video)
                if favorites:
                    label += f"; ★{favorites}"
                url = photo_url(group[0], link, library, thumb_dir, thumb_size)
                marker = f"[{label}]({url})" if url else f"*({label})*"
                text = sentence(caption) if caption else ""
                bullet = f"- {_fmt_range(group)} — " + (f"{text} " if text else "")
                bullet += marker
                if tags and index == 0:
                    bullet += f" — {tags}"
                lines.append(bullet)
    lines.append("")
    return "\n".join(lines)


_HTML_STYLE = (
    "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
    "max-width:52rem;margin:0 auto;padding:2rem 1.25rem;line-height:1.5;color:#1f2328}"
    "h1{font-size:1.7rem;margin:0 0 .25rem}h2{margin:2rem 0 .5rem}"
    "h3{margin:1.5rem 0 .5rem;color:#57606a}h4{margin:1.25rem 0 .25rem}"
    ".meta{color:#57606a;margin:0 0 .5rem}.note{color:#57606a;font-size:.9rem;"
    "margin:0 0 1.5rem}.loc{margin:.75rem 0 .25rem;font-weight:600}"
    ".narrative{font-style:italic;color:#424a53;margin:.25rem 0 .75rem}"
    "ul{margin:.25rem 0 .75rem;padding-left:1.25rem}li{margin:.15rem 0}"
    ".time{color:#57606a;font-variant-numeric:tabular-nums}"
    ".tags{color:#57606a;font-size:.9rem}a{color:#0969da;text-decoration:none}"
    "a:hover{text-decoration:underline}"
    "@media(prefers-color-scheme:dark){body{background:#0d1117;color:#e6edf3}"
    "h3,.meta,.note,.time,.tags{color:#8b949e}.narrative{color:#c9d1d9}"
    "a{color:#58a6ff}}"
)


def render_html(
    batches: list[Batch],
    windows: list[tuple[str, str]] | None = None,
    similarity: float = DEFAULT_SIMILARITY,
    link: str = "thumb",
    library: Path | None = None,
    thumb_dir: Path | None = None,
    thumb_size: int = DEFAULT_THUMB_SIZE,
    narrative: bool = True,
) -> str:
    """Render the journal as a standalone HTML page (browser-native)."""
    grouped = [(batch, cluster_entries(batch.entries, similarity)) for batch in batches]
    if link == "thumb":
        examples = (group[0] for _, clusters in grouped for _, group in clusters)
        _materialize_thumbs(examples, library, thumb_dir, thumb_size)
    total = sum(len(batch.entries) for batch in batches)
    out = [
        "<!DOCTYPE html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>Photo Journal — Apple Photos</title>",
        f"<style>{_HTML_STYLE}</style></head><body>",
        "<h1>Photo Journal — Apple Photos</h1>",
    ]
    if batches:
        out.append(
            f'<p class="meta">{total:,} owner photos · '
            f"{batches[0].start:%b %-d, %Y} – {batches[-1].end:%b %-d, %Y}</p>"
        )
    out.append(
        '<p class="note">The owner\'s camera photos only, in capture order. '
        f"Owner's phone: {html.escape(format_owner_phones(windows or []))}. "
        "Screenshots, screen recordings, saved/shared images, other people's "
        "phones, and DSLRs are excluded. Locations are colloquial names from "
        "reverse-geocoded place data; descriptions come from Apple's on-device "
        "ML captions, labels, and OCR. &#9733; marks a favorite.</p>"
    )

    day_cameras: dict = {}
    for batch in batches:
        cameras = day_cameras.setdefault(batch.start.date(), [])
        for camera in dict.fromkeys(entry.camera for entry in batch.entries):
            if camera not in cameras:
                cameras.append(camera)

    current_year = current_month = None
    current_location = None
    open_list = False

    def close_list() -> None:
        nonlocal open_list
        if open_list:
            out.append("</ul>")
            open_list = False

    home = home_coord(batches)
    trip = TripState()
    for day, items in _day_groups(grouped):
        if day.year != current_year:
            current_year = day.year
            current_month = None
            close_list()
            out.append(f"<h2>{current_year}</h2>")
        if (day.year, day.month) != current_month:
            current_month = (day.year, day.month)
            close_list()
            out.append(f"<h3>{html.escape(day.strftime('%B %Y'))}</h3>")
        close_list()
        cameras = html.escape(", ".join(day_cameras[day]))
        out.append(f"<h4>{html.escape(day.strftime('%A, %B %-d'))} · {cameras}</h4>")
        if narrative:
            text, trip = day_narrative(day, items, home, trip)
            out.append(f'<p class="narrative">{html.escape(text)}</p>')

        current_location = None
        for batch, clusters in items:
            if batch.location != current_location:
                current_location = batch.location
                close_list()
                out.append(f'<p class="loc">{html.escape(current_location)}</p>')
            if not open_list:
                out.append("<ul>")
                open_list = True

            tags = _tag_suffix(batch)
            for index, (caption, group) in enumerate(clusters):
                count = len(group)
                favorites = sum(entry.favorite for entry in group)
                video = any(entry.video for entry in group)
                label = count_note(count, video) + (f"; ★{favorites}" if favorites else "")
                url = photo_url(group[0], link, library, thumb_dir, thumb_size)
                if url:
                    marker = f'<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>'
                else:
                    marker = f"<em>({html.escape(label)})</em>"
                text = html.escape(sentence(caption)) if caption else ""
                item = f'<li><span class="time">{html.escape(_fmt_range(group))}</span> &mdash; '
                item += (f"{text} " if text else "") + marker
                if tags and index == 0:
                    item += f' <span class="tags">&mdash; {html.escape(tags)}</span>'
                item += "</li>"
                out.append(item)
    close_list()
    out.append("</body></html>")
    return "\n".join(out) + "\n"


def build_journal(
    photos: Iterable[Photo],
    gap_minutes: int = DEFAULT_GAP_MINUTES,
    similarity: float = DEFAULT_SIMILARITY,
    location_degrees: float = DEFAULT_LOCATION_DEGREES,
    link: str = "thumb",
    library: Path | None = DEFAULT_LIBRARY,
    html: bool = False,
    thumb_dir: Path | None = None,
    thumb_size: int = DEFAULT_THUMB_SIZE,
    keep_noise: bool = False,
    narrative: bool = True,
    owner_name: set[str] | None = None,
) -> str:
    """Select the owner's photos and render the full journal."""
    photos = list(photos)
    windows = infer_owner_windows(photos)
    selected = select_owner_photos(photos, windows, keep_noise)
    batches = build_batches(selected, gap_minutes, location_degrees, owner_name)
    renderer = render_html if html else render
    return renderer(batches, windows, similarity, link, library, thumb_dir,
                    thumb_size, narrative)


def read_photos(paths: Iterable[Path]) -> list[Photo]:
    """Load :class:`Photo` objects from ``photos.sqlite`` or ``photos.json``."""
    photos: list[Photo] = []
    for path in paths:
        path = Path(path)
        if path.suffix.lower() == ".json":
            with path.open(encoding="utf-8") as fh:
                photos.extend(Photo.from_record(r) for r in json.load(fh))
            continue
        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                "SELECT uuid, filename, date_original, camera_model, screenshot, "
                "place_name, place_address, latitude, longitude, ai_caption, labels, "
                "persons, favorite, media_type, search_info FROM photos"
            ).fetchall()
        finally:
            con.close()
        photos.extend(Photo.from_row(row) for row in rows)
    return photos
