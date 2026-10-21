# photojournaler

[![CI](https://github.com/ctkolb/photojournaler/actions/workflows/ci.yml/badge.svg)](https://github.com/ctkolb/photojournaler/actions/workflows/ci.yml)

Turn an Apple Photos library into a readable **photo journal**.

**photojournaler** reads your local macOS Photos library, works out which photos
are actually yours (taken with your phone, not saved or shared), and writes a
journal: a short story for every day, grouped by place, with a link to an
example photo. It never moves, edits, uploads, or resizes your photos.

> **Requirements:** macOS with the Photos app. The library is read through
> Photos' own database (with [osxphotos](https://github.com/RhetTbull/osxphotos)),
> so photos that live only in iCloud still contribute whatever metadata the
> database already holds. No photos are downloaded.

## Quick start

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Export your library's metadata into ./out
.venv/bin/python export_photos_metadata.py

# Build the journal
.venv/bin/python build_journal.py --out out/journal.md

# …or a self-contained page you can open in a browser
.venv/bin/python build_journal.py --out out/journal.html

# …or per-year Obsidian notes (a day index + a trip index)
.venv/bin/python build_journal.py --vault
```

## Vault output

`--vault` writes a set of Obsidian-friendly notes into `out/vault/` instead of a
single file:

- **`<year>.md`** per year — each opens with a compact **day index** of inline
  Dataview fields, one entry per day, followed by the day-by-day entries:

  ```markdown
  - date:: 2026-04-11 · weekday:: Saturday · device:: iPhone 16 Pro · trip:: prague-paris-2026 · away:: true
    places:: 16th Arr. — Paris (48.8566, 2.3522); Eiffel Tower — Paris (48.8584, 2.2945) · people:: Casey · photos:: 41 · videos:: 6 · favorites:: 2 · tz:: UTC+02:00
  ```

  Fields: ISO `date`, `weekday`, `device`, `trip` (slug), `away` (far from home),
  `places` (colloquial name + coordinates), `people`, `photos`, `videos`,
  `favorites`, `activities`, `holidays`, and `tz` (the day's timezone(s)).
- **`trips.md`** — the trip index: each contiguous away-from-home span (at least
  two days, or any day touching an airport or a foreign country) with its start,
  end, day count, ordered route, people, and photo/video totals.

Vault mode defaults to `--link none` and `--no-narrative`, so counts are plain
and the prose is omitted (the day index carries the summary).

## What the journal looks like

```markdown
### May 2024

#### Monday, May 27 · iPhone 13 Pro

On Monday, May 27, they set out for Central Station — Lakeside, IL. In the morning
they were at home in Fairview, TN with Riley, taking photos of a person holding a
napkin. In the afternoon they were at Terminal E — Northgate, TN, taking photos of
a sign with a number on a pillar. At night they were at Central Station — Lakeside,
IL, taking photos of a city street at night with people walking on the sidewalk.
It was Memorial Day.

**Home — Fairview, TN**
- 9:48 AM–10:10 AM — A close up of a person holding a napkin [3 photos/videos](photos/70A24653-…jpg) — Memorial Day · with Riley

**Terminal E — Northgate, TN**
- 12:20 PM — A sign with a number on it on a pillar in a parking lot [1 photo](photos/D4ACDFE4-…jpg) — Memorial Day

**Central Station — Lakeside, IL**
- 8:56 PM — A city street at night with people walking on the sidewalk [2 photos](photos/70A24653-…jpg) — Memorial Day

#### Tuesday, May 28 · iPhone 13 Pro

On Tuesday, May 28, the trip continued around Lakeside, IL. In the morning they
were at Lakeside River, taking photos of a bridge over a body of water. In the
afternoon they were at Northside Transit Center — Lakeside, IL, taking photos of a
train station with a large advertisement on the side of it. …
```

The journal is written to a single running file, grouped
**year → month → day**, with a bold location line per place and one bullet per
caption cluster.

## What gets included

- **Only your phone.** Your phone is inferred from the data (see below); other
  people's phones and DSLRs are dropped. Pass `--no-speculative-id` to include
  every camera instead.
- **Only camera photos.** Screenshots, screen recordings, saved GIFs, and images
  received via Messages/WhatsApp/etc. are excluded.
- **No clutter.** Low-value shots — documents, barcodes, receipts, menus, and
  boxes — are dropped (`--keep-junk` keeps them).
- **No duplicates.** Album-export copies are collapsed so counts reflect real
  captures.
- **Location from nearby shots.** A photo with no location adopts the location of
  the nearest located photo within `--inherit-minutes` (default 5; `0` disables)
  — from your phone only, or from any camera with `--no-speculative-id`. A photo
  that has GPS but no place name has its place guessed from the nearest located
  photo within `--location-guess-km` kilometers (default 0.1; `0` disables).

## How the journal is built

The pipeline is deterministic and lives in `photosmeta/journal.py`:

1. **Owner inference.** Each month, the most common *numeric iPhone* is treated
   as the owner's; the owner's model is only allowed to move to a newer
   generation, then tier (`Pro Max` > `Pro` > `Plus` > base). On a typical
   library this yields a progression like
   `iPhone 13 Pro → iPhone 16 Pro (Sep 20, 2024) → iPhone 17 Pro Max (Jun 7, 2026)`.
2. **Selection & de-duplication**, as described above.
3. **Batching.** Photos at the same place within a 75-minute gap are grouped.
   A new place starts when you move more than `--location-degrees` (~0.5 km).
4. **Clustering.** Near-identical and reworded captions collapse into one line,
   comparing *content words*. `--similarity` tunes how aggressively (lower
   groups more; too low merges different subjects).
5. **Places.** Locations are colloquial and as specific as the data allows:
   venues, landmarks, areas of interest, bodies of water, and neighborhoods
   win, then the leading place name (e.g. `Millennium Station`), then the
   street. Highways are skipped. Home is rendered as `Home` (configure it with
   `--home-label`, `--home-address`, and `--home-place`).
6. **Narrative.** See below.
7. **Photo links.** See below.

Descriptions come only from Apple's on-device ML (`ai_caption`, labels, OCR).
**The journal never invents or editorializes.**

### The daily narrative

When enabled, each day opens with a story told like you'd tell a friend: where
you were, who you were with, and what you did — in order. It is built *only*
from that day's metadata (places and times, people/pets, activities and
holidays, and the day's most common captions), so nothing is made up.

- **Farther from home ⇒ longer story.** Distance is measured from your home
  coordinate, so a day trip or vacation gets more detail than a day around the
  house.
- **Trips continue, they don't restart.** Consecutive days far from home and near
  each other read as one trip (`they set out for …` → `the trip continued
  around …` → `they were back home`).

The narrative is **off by default** (vault mode omits it in favor of the day
index). Turn it on with `--narrative`. Pass `--owner-name "Your Name"` to keep
your own name out of the `with …` lists.

### Opening a photo

Each count can link to one example photo from that line. The default is `none`
(plain counts), which is what an Obsidian vault wants — `--link thumb` instead
copies a small preview into `out/photos/` and links it **relatively**
(`photos/<uuid>.jpg`), which works in VS Code's Markdown preview and in a
browser but breaks in a vault that has no such folder. Other modes: `photos`
opens the Photos app (`photos://asset?identifier=<uuid>`); `preview`/`original`
link the file inside the library with `file://` (some viewers won't clickify
these, and macOS privacy protection blocks the browser from reading
`~/Pictures`). Anything without a local file falls back to the `photos` scheme.

## Journal options

Every option can also be set in a YAML config (see below); a flag overrides the
config, and the config overrides the defaults.

| Flag | Description |
|---|---|
| `inputs` | One or more `photos.sqlite` / `photos.json` exports (default: `out/photos.sqlite`) |
| `--out PATH` | Output file; a `.html` path writes a browser-native page (default: `out/journal.md`) |
| `--vault` / `--no-vault` | Write per-year Obsidian notes plus `trips.md` into `out/vault/` instead of a single file |
| `--similarity F` | Caption-grouping threshold; lower groups more (default: 0.5) |
| `--gap-minutes N` | Split a batch after this idle gap (default: 75) |
| `--location-degrees F` | Start a new place after moving this far, in degrees (~0.5 km; default: 0.005) |
| `--link MODE` | Link each count: `thumb`, `preview`, `original`, `photos`, `none` (default: `none`) |
| `--library PATH` | Photos library used to resolve links (default: `~/Pictures/Photos Library.photoslibrary`) |
| `--thumb-size N` | Long-edge pixels for `--link thumb` (default: 400) |
| `--speculative-id` / `--no-speculative-id` | Infer the owner's phone and keep only its photos (default), or `--no-…` to include every camera |
| `--inherit-minutes N` | Give a location-less photo the nearest located photo's place within N minutes (default: 5; `0` disables) |
| `--location-guess-km F` | Guess the place of a GPS-only photo from the nearest located photo within F km (default: 0.1; `0` disables) |
| `--keep-junk` / `--no-keep-junk` | Keep low-value shots (documents, barcodes, receipts, menus, boxes); dropped by default |
| `--narrative` / `--no-narrative` | Write the per-day story, or omit it (default: omitted) |
| `--owner-name NAME` | Person to omit from `with …` (the journal owner; repeatable) |
| `--home-label LABEL` | Label printed for photos taken at home (default: `Home`) |
| `--home-address PREFIX` | Address prefix that marks a photo as taken at home (repeatable) |
| `--home-place NAME` | Place name that marks a photo as taken at home (repeatable) |
| `--quiet` | Suppress the summary |

### Configuration

Any option can live in a YAML file, keyed by the flag name:

```yaml
vault: true
link: none
narrative: false
gap-minutes: 75
speculative-id: true
location-guess-km: 0.1
owner-name: ["Your Name"]
home-label: Home — Fairview, TN
home-address: ["100 Example St"]
home-place: ["Fairview"]
```

- `--config PATH` uses that config; otherwise `./config.yaml` is used when it
  exists, and settings it omits fall back to the defaults.
- Flags override only the config keys they specify.
- `--generate-config [PATH]` writes a config from the defaults (with any given
  flags applied) and exits — `config.yaml` by default. It does not build the
  journal.

Build the journal from several per-year exports in one running file:

```sh
.venv/bin/python build_journal.py \
  out/2024/photos.sqlite out/2025/photos.sqlite out/photos.sqlite \
  --out out/journal.md --owner-name "Your Name"
```

## Exporting the metadata (used by the journal)

`export_photos_metadata.py` writes everything the journal needs — and a lot more
— to JSON, CSV, and SQLite.

```sh
.venv/bin/python export_photos_metadata.py                     # all formats into ./out
.venv/bin/python export_photos_metadata.py --formats sqlite    # just SQLite
.venv/bin/python export_photos_metadata.py --since 2024-01-01 --until 2024-12-31
.venv/bin/python export_photos_metadata.py --album "Favorites" --person "Jordan"
```

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

Each asset captures identity, time, place, EXIF, video, image dimensions, flags
(favorite, screenshot, live photo, …), albums/keywords, people, and Apple's ML
(`ai_caption` + confidence, normalized labels, OCR text, and the full
search-index dictionary: activities, pets, landmarks, holidays, seasons, and
more).

### Output files

- `photos.json` — one nested object per asset, full fidelity
- `photos.csv` — one flat row per asset
- `photos.sqlite` — a `photos` table plus join tables, indexed on date, country,
  and camera
- `journal.md` / `journal.html` — the generated journal (`out/photos/` holds its
  preview images)

## Analyzing an export

`analyze_photos_metadata.py` prints a summary and can render a self-contained
HTML report (calendar heatmap, rankings for year, camera, country, people,
labels, albums):

```sh
.venv/bin/python analyze_photos_metadata.py out/photos.sqlite --html report.html
```

## Tests

The suite is hermetic — duck-typed fakes instead of a real Photos library — and
runs anywhere in under a second:

```sh
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

## Continuous integration

`.github/workflows/ci.yml` runs the tests on every push and PR to `main`, on a
macOS runner (required, since importing `osxphotos` needs macOS). The job is
named **`test`**; that is the required status check enforced by branch
protection (see [`docs/branch-protection.md`](docs/branch-protection.md)).

## Design principles

- **AI-assisted development, disclosed.** Contributions written with AI help are
  reviewed like any other and disclosed with an `Assisted-by:` commit trailer
  (see [`CONTRIBUTING.md`](CONTRIBUTING.md)).

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for how to contribute, including the
`Assisted-by:` trailer for disclosing AI assistance. See [`AGENTS.md`](AGENTS.md)
for the repository layout, the journal pipeline internals, and conventions.

## License

Licensed under the **GNU General Public License, version 3 or later**
(`GPL-3.0-or-later`). See [`LICENSE`](LICENSE) for the full text.

You are free to use, study, share, and modify this software. If you
distribute it, or a work based on it, you must pass on the same freedoms
and make the corresponding source available under the same license.

This program is distributed in the hope that it will be useful, but
**without any warranty**; without even the implied warranty of
merchantability or fitness for a particular purpose.
