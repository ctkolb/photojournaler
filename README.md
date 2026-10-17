# photojournaler

[![CI](https://github.com/ctkolb/photojournaler/actions/workflows/ci.yml/badge.svg)](https://github.com/ctkolb/photojournaler/actions/workflows/ci.yml)

Turn an Apple Photos library into a readable **photo journal**: a short story
for every day, grouped by place, with links to example photos.

photojournaler isn't a click-to-run app. It's a small, deterministic **engine**
plus guidance, meant to be driven by an **AI coding agent** (Claude Code,
Cursor, opencode, Codex, …). You point your agent at this repo, describe the
journal you want, and the agent sets everything up, runs the pipeline, and
writes the journal for you. The engine underneath is plain scripts, so any run
is reproducible — but you're not expected to type the commands yourself.

> **Requirements:** a Mac with the **Photos** app. The library is read through
> Photos' own database (via [osxphotos](https://github.com/RhetTbull/osxphotos)),
> so photos that live only in iCloud still contribute whatever metadata the
> database already holds. No photos are downloaded, moved, edited, or uploaded.

## Getting started

1. Open this repository in your AI coding agent of choice.
2. Ask it to build your journal. For example:

   > Read `AGENTS.md`, then build a photo journal from my Photos library into
   > `out/journal.md`. Skip screenshots and junk, and don't include my own name
   > in the "with …" lists.

3. The agent will create the virtual environment, install dependencies, export
   your library's metadata, and write the journal. When it's done you'll have
   `out/journal.md` (and, if you ask, a self-contained `out/journal.html`).

You stay in the loop: tell the agent to change the format, group more/less,
include multiple years, link photos differently, and so on. `AGENTS.md` in this
repo tells the agent how the engine works so it can do this without guessing.

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

The journal is a single running file, grouped **year → month → day**, with a
bold location line per place and one line per cluster of similar photos. Each
count links to one example photo.

## What goes in — and what doesn't

- **Only your phone.** Your phone is inferred from the data; other people's
  phones and DSLRs are dropped.
- **Only camera photos.** Screenshots, screen recordings, saved GIFs, and images
  received via Messages/WhatsApp/etc. are excluded.
- **No clutter.** Low-value shots — documents, barcodes, receipts, menus, and
  boxes — are dropped.
- **No duplicates.** Album-export copies are collapsed so counts reflect real
  captures.
- **Grounded descriptions.** Every phrase comes from Apple's on-device ML
  captions, labels, and OCR. **Nothing is invented or editorialized.**

## Under the hood

The engine is deterministic and lives in `photosmeta/journal.py`. The
`build_journal.py` CLI wires it together. Given the same library, it always
produces the same journal.

1. **Owner inference.** Each month, the most common *numeric iPhone* is treated
   as the owner's; the owner's model only ever moves to a newer generation, then
   tier (`Pro Max` > `Pro` > `Plus` > base). Typical result:
   `iPhone 13 Pro → iPhone 16 Pro (Sep 20, 2024) → iPhone 17 Pro Max (Jun 7, 2026)`.
2. **Selection & de-duplication** (see above).
3. **Batching.** Photos at the same place within a 75-minute gap are grouped; a
   new place starts once you move more than ~0.5 km.
4. **Clustering.** Near-identical and reworded captions collapse into one line,
   comparing *content words*.
5. **Places.** Colloquial and as specific as the data allows: venues, landmarks,
   areas of interest, bodies of water, and neighborhoods win, then the leading
   place name (e.g. `Millennium Station`), then the street. Highways are skipped,
   and home is rendered as `Home — Fairview, TX`.
6. **Narrative.** A story for each day, told in order (where / who / what) and
   built only from that day's metadata. Days far from home get more detail, and
   consecutive far-off days read as one continuing trip instead of restarting.
7. **Photo links.** Each count links to one example photo.

### Files

| Path | Purpose |
|---|---|
| `photosmeta/journal.py` | The journal engine (owner inference, selection, batching, clustering, places, narrative, rendering) |
| `build_journal.py` | CLI that drives the engine and writes `out/journal.md` or `out/journal.html` |
| `export_photos_metadata.py` | Exports the library's metadata to JSON/CSV/SQLite |
| `analyze_photos_metadata.py` | Prints a summary / renders an HTML stats report |
| `AGENTS.md` | Guidance the agent reads (layout, pipeline, conventions) |
| `out/` | Generated output (`journal.md`, `photos/` previews, exports) |

## Running the scripts directly (optional)

The agent normally runs these for you, but the engine is usable on its own:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Export your library's metadata into ./out
.venv/bin/python export_photos_metadata.py

# Build the journal (markdown), or pass --out out/journal.html for a web page
.venv/bin/python build_journal.py --out out/journal.md
```

### Journal options

| Flag | Description |
|---|---|
| `inputs` | One or more `photos.sqlite` / `photos.json` exports (default: `out/photos.sqlite`) |
| `--out PATH` | Output file; a `.html` path writes a browser-native page (default: `out/journal.md`) |
| `--similarity F` | Caption-grouping threshold; lower groups more (default: 0.5) |
| `--gap-minutes N` | Split a batch after this idle gap (default: 75) |
| `--location-degrees F` | Start a new place after moving this far, in degrees (~0.5 km; default: 0.005) |
| `--link MODE` | Link each count: `thumb`, `preview`, `original`, `photos`, `none` (default: `thumb`) |
| `--library PATH` | Photos library used to resolve links (default: `~/Pictures/Photos Library.photoslibrary`) |
| `--thumb-size N` | Long-edge pixels for `--link thumb` (default: 400) |
| `--keep-junk` | Keep low-value shots (documents, barcodes, receipts, menus, boxes) |
| `--no-narrative` | Omit the per-day story |
| `--owner-name NAME` | Person to omit from `with …` (the journal owner; repeatable) |

`--link thumb` (the default) copies a small preview into `out/photos/` and links
it relatively, which works in VS Code's preview and in a browser. `photos` uses
the Photos app URL scheme; `preview`/`original` link the file inside the library
with `file://` (often blocked, because macOS protects `~/Pictures`).

### Export options

`export_photos_metadata.py` writes everything the journal needs — and more — to
JSON, CSV, and SQLite (`--formats`, `--since/--until`, `--album/--person/--keyword`,
`--no-ml`, …). `analyze_photos_metadata.py` summarizes an export or renders a
self-contained HTML report (calendar heatmap, rankings for year, camera,
country, people, labels, albums):

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
named **`test`**, the required status check enforced by branch protection (see
[`docs/branch-protection.md`](docs/branch-protection.md)).

## For contributors and agents

See [`AGENTS.md`](AGENTS.md) for the repository layout, the journal pipeline
internals, and conventions.
