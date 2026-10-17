# AGENTS.md

Guidance for agents (and humans) working in this repository.

## What this is

`photojournaler` reads a local macOS Photos library through
[osxphotos](https://github.com/RhetTbull/osxphotos) and:

1. **Exports** rich per-asset metadata to JSON, CSV, and SQLite.
2. **Analyzes** an export (console summary + optional HTML report).
3. **Builds a markdown photo journal** from the library owner's camera photos.

It never downloads, modifies, or exports photos. Assets stored only in iCloud
still contribute whatever metadata the database already holds.

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
```

## Layout

| Path | Purpose |
|---|---|
| `photosmeta/extract.py` | `osxphotos` `PhotoInfo` → nested record |
| `photosmeta/schema.py` | Flatten records; derive many-to-many relations |
| `photosmeta/writers.py` | Streaming JSON / CSV / SQLite writers |
| `photosmeta/journal.py` | Deterministic journal pipeline (owner inference, filtering, grouping, rendering) |
| `export_photos_metadata.py` | CLI: Photos library → `out/photos.{json,csv,sqlite}` |
| `analyze_photos_metadata.py` | CLI: export → console summary / HTML report |
| `build_journal.py` | CLI: export(s) → `out/journal.md` |
| `tests/` | Hermetic pytest suite (duck-typed fakes; no real library) |
| `out/` | Generated outputs (gitignored) |

## Commands

```sh
# Export everything into ./out
.venv/bin/python export_photos_metadata.py

# Summary / HTML report
.venv/bin/python analyze_photos_metadata.py out/photos.sqlite --html report.html

# Build the journal (defaults to out/photos.sqlite)
.venv/bin/python build_journal.py --out out/journal.md

# Build from multiple per-year exports
.venv/bin/python build_journal.py \
  out/2024/photos.sqlite out/2025/photos.sqlite out/photos.sqlite --out out/journal.md

# Tests
.venv/bin/python -m pytest
```

## The journal pipeline

`photosmeta/journal.py` is deterministic and split into small functions. The
`build_journal.py` CLI wires them together. When changing journal behavior,
keep the steps separable and covered by `tests/test_journal.py`.

1. **Owner inference** — `infer_owner_windows(photos)`.
   For each calendar month, the most common *numeric iPhone* is taken as the
   owner's (ties favor the newer model). The owner's model may only move to a
   newer generation, then tier (`Pro Max` > `Pro` > `Plus` > base). Each window
   starts on the first date that model appears. Non-phones (Canon, Nikon,
   `iPhone SE`, …) never become the owner's.
2. **Selection** — `select_photos(photos, windows, owner, keep_noise, inherit_minutes)`.
   Keeps camera photos (by default only the owner's phone; `--no-owner` keeps
   every camera). Excludes screenshots, screen recordings, saved/shared images
   (the `source` field, e.g. `Messages`, `WhatsApp`), and low-value shots
   (`is_noise`). A photo with no location adopts the location of the nearest
   located photo within `inherit_minutes` (default 5; 0 disables) — from the
   owner's phone only when `owner` is set, from any camera otherwise.
   De-duplicates album-export copies by `(date_original, camera_model)`.
3. **Batching** — `build_batches(photos, gap_minutes, location_degrees)`.
   Groups consecutive photos at the same place when the idle gap is within
   `gap_minutes` (default 75) and the camera has not moved more than
   `location_degrees` (~0.5 km; default 0.005) from where the place began.
   Photos missing GPS inherit the surrounding location within the gap so
   outings don't fragment.
4. **Clustering** — `cluster_entries(entries, similarity)`.
   Within a batch, captions are clustered by content-word string similarity
   (default 0.5) so near-identical and reworded shots collapse into one line.
   Caption-less photos fall back to their ML labels.
5. **Rendering** — `render` (Markdown) or `render_html` (a browser-native page).
   Year → month → day headings, a bold location line per place, and one bullet
   per caption cluster with a count and a `★` favorite marker. Tags add
   activities, holidays, and people/pets. Camera model is shown per day. Each
   count is a link to one example photo by `photo_url`. The `build_journal.py`
   CLI picks the format from the `--out` extension (`.html` → HTML).
6. **Narrative** — `day_narrative(day, items, home, previous)`.
   A short story per day, told in order (where / who / what), derived only from
   that day's metadata. Days far from home get more detail, and consecutive
   far-off days read as one continuing trip (`TripState` is threaded from day to
   day). Disable with `--no-narrative`; omit the owner's own name from `with …`
   with `--owner-name`.

### Conventions baked into the journal

- Locations are **colloquial** and as specific as the data allows:
  `location_label` prefers named venues, landmarks, areas of interest, bodies of
  water, and neighborhoods, then the leading place name (e.g. `Millennium
  Station`), then the street, and only then the town. The home address is
  rendered as `Home — Fairview, TX` (`HOME_ADDR` / `HOME_LABEL`).
- A day is not one location: a new place is emitted once the camera moves more
  than `location_degrees` (~0.5 km) from the current place's anchor.
- Descriptions come from Apple's on-device ML (`ai_caption`, labels, OCR) —
  never invented. The journal does not editorialize.
- Each count links to one example photo via `photo_url`. The default `thumb`
  mode copies a small `sips`-downscaled preview into `out/photos/` and links it
  relatively (works in VS Code and browsers); `photos` uses
  `photos://asset?identifier=<uuid>` (opens the Photos app); `preview`/`original`
  link `file://` inside the library (often blocked); `none` is plain.
  Unresolved links fall back to the `photos` scheme.
- Output is a single running file (`out/journal.md`), grouped for navigation.

## Testing

The suite is hermetic and fast; it uses duck-typed fakes instead of a real
Photos library, so it runs anywhere:

```sh
.venv/bin/python -m pytest
```

When adding behavior, prefer small pure functions in `photosmeta/` and cover
them in `tests/`. `tests/test_journal.py` builds `photosmeta.journal.Photo`
objects directly; `tests/conftest.py` provides `make_photo` for the extraction
pipeline.

## Conventions

- American English everywhere (behavior, color, center, license, …).
- Never work on `main`; create a branch before committing.
- Never run `git push` or any remote/PR operation — stop at the local commit.
- **Commit often**, in small, focused steps as work progresses — one logical
  change per commit (a bug fix, a new option, a docs update), not one big commit
  at the end. Each message should say what changed.
