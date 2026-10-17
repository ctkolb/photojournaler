# PhotosMetadata

[![CI](https://github.com/ctkolb/PhotosMetadata/actions/workflows/ci.yml/badge.svg)](https://github.com/ctkolb/PhotosMetadata/actions/workflows/ci.yml)

Export rich metadata from a macOS Photos library (including iCloud Photos) to
**JSON**, **CSV**, and **SQLite**.

It reads the local `Photos.sqlite` database through
[osxphotos](https://github.com/RhetTbull/osxphotos). It never downloads,
modifies, or exports your photos — assets stored only in iCloud still
contribute whatever metadata the database already holds.

## Install

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Usage

```sh
# Everything: JSON + CSV + SQLite into ./out
.venv/bin/python export_photos_metadata.py

# Just CSV, into a custom folder
.venv/bin/python export_photos_metadata.py --formats csv --outdir ~/metadata

# Only 2024 photos, skip the ML pass for speed
.venv/bin/python export_photos_metadata.py --since 2024-01-01 --until 2024-12-31 --no-ml

# Filter by album / person / keyword
.venv/bin/python export_photos_metadata.py --album "Favorites" --person "Madison"

# Non-default library, quick smoke test
.venv/bin/python export_photos_metadata.py --library "/Volumes/Photos.photoslibrary" --limit 1000
```

### Options

| Flag | Description |
|---|---|
| `--library PATH` | `.photoslibrary` to read (default: system library) |
| `--outdir DIR` | Output directory (default: `./out`) |
| `--prefix NAME` | Output filename prefix (default: `photos`) |
| `--formats LIST` | Any of `json,csv,sqlite` (default: all) |
| `--album` / `--person` / `--keyword` | Filter (repeatable) |
| `--since` / `--until` | Capture-date range, `YYYY-MM-DD` |
| `--limit N` | Stop after N assets (testing) |
| `--no-ml` | Skip captions, labels, image analysis (faster) |
| `--quiet` | No progress output |

Exports run at roughly 400 assets/second after an ~11 s library load, so a
~118k-asset library takes a few minutes.

## Metadata collected

- **Identity** — UUID, filename, original filename, media type, original file size
- **Time** — capture date, original date, date added, date modified, timezone name + UTC offset
- **Place** — latitude, longitude, reverse-geocoded name, street address, country, full place hierarchy
- **Camera / EXIF** — make, model, lens, focal length, aperture, shutter speed, ISO, exposure bias, flash, metering mode, white balance
- **Video** — codec, frame rate, duration, bit rate, sample rate, track format
- **Image** — width/height (current + original), orientation
- **Flags** — favorite, hidden, live photo, burst, panorama, screenshot, selfie, portrait, HDR, slow-mo, time-lapse, spatial, RAW, edited (adjustments / external edit), shared, in iCloud, missing, trashed, and more
- **Organization** — albums, album folders, keywords, title, description, star rating
- **People** — named people detected in the photo
- **Machine learning** — Apple's `ai_caption` with confidence, normalized scene/object labels, detected (OCR) text, and the full Apple search-index dictionary (activities, pets, landmarks, events, seasons, times of day, age groups, and more)

## Output

- `photos.json` — one nested object per asset, full fidelity
- `photos.csv` — one flat row per asset; list fields joined with `, `, complex
  fields (`search_info`, `place_names`) stored as JSON strings
- `photos.sqlite` — a `photos` table plus `albums`, `persons`, `keywords`,
  `labels` tables and their `photo_*` join tables, with indexes on date,
  country, and camera

### Example queries

```sql
-- Photos per camera model
SELECT camera_model, COUNT(*) FROM photos GROUP BY 1 ORDER BY 2 DESC;

-- Most-photographed people
SELECT p.name, COUNT(*) c FROM photo_persons pp
JOIN persons p ON p.id = pp.person_id GROUP BY 1 ORDER BY c DESC;

-- Photos taken per country
SELECT place_country_code, COUNT(*) FROM photos
WHERE place_country_code IS NOT NULL GROUP BY 1 ORDER BY 2 DESC;
```

## Analyze

`analyze_photos_metadata.py` reads either output file and prints a summary,
optionally rendering a self-contained HTML report (inline CSS + SVG, no extra
dependencies).

```sh
# Console summary from the SQLite export
.venv/bin/python analyze_photos_metadata.py out/photos.sqlite

# Also write an HTML report with bar charts and a calendar heatmap
.venv/bin/python analyze_photos_metadata.py out/photos.sqlite --html report.html

# Works from JSON too; show 25 rows per ranking
.venv/bin/python analyze_photos_metadata.py out/photos.json --top 25
```

It reports asset counts, date range, GPS/people/favorite coverage, and rankings
for year, time of day, weekday, camera, country, people, ML labels, and albums.
The HTML report adds a GitHub-style calendar heatmap of daily capture counts.
Times are local wall-clock, derived from each asset's original timestamp with
its UTC offset preserved.

## Tests

The suite is hermetic — it uses duck-typed fakes instead of a real Photos
library, so it runs anywhere in well under a second.

```sh
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

## Continuous integration

`.github/workflows/ci.yml` runs the test suite on every push and pull request
targeting `main`, on a macOS runner (required, since importing `osxphotos`
needs macOS). The single job is named **`test`**; that name is the required
status check enforced by branch protection.

To block merges to `main` until `test` passes, create a repository ruleset (see
[`docs/branch-protection.md`](docs/branch-protection.md)).
