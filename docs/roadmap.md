# Roadmap

Potential future improvements for photojournaler. When an item is picked up,
move it into a GitHub issue (for tracked work) or check it off here.

## Selected / requested

_None yet._

## AI-proposed ideas

Grouped suggestions, not yet selected. `[hardcoded]` marks something that is
currently a constant in the code (so it's a small change to expose).

**Top recommendations:** `since`/`until`, `exclude-albums`, `cameras`,
`videos`, `day-start-hour`, `pronoun` — these cover the most variation across
users for the least complexity.

### Identity & scope

- `since` / `until` — build a journal for a date range only (or `years: [...]`).
- `include-albums` / `exclude-albums` — keep to, or drop, named albums.
- `cameras` — explicitly allow/deny camera models (e.g. include a DSLR or a
  second phone). Pairs with an `owner-cameras` override for when
  `speculative-id` guesses wrong.
- `videos` — include/exclude videos.
- `screenshots` / `saved-images` — make the current exclusion optional.

### Location & places

- `home-radius-km` — treat photos within this radius of home as "home".
- `skip-highways` — currently always skips road-like names (`I-280`, `Fwy`).
  `[hardcoded]`
- `place-priority` — tune the order (venue → landmark → area → neighborhood →
  street → town).
- `neighborhood-detail` — prefer or avoid neighborhood-level names.

### Selection & noise

- `noise-labels` / `noise-patterns` — customize the junk blacklist (documents,
  barcodes, menus, …). `[hardcoded]`
- `min-photos-per-day` — skip days with too few photos.
- `min-cluster-size` — drop one-off lines below a threshold.

### Grouping & day boundaries

- `day-start-hour` — when a "day" begins (e.g. `4`, so a 1 a.m. shot belongs to
  the night before).
- `max-lines-per-place` — cap lines per place, summarizing the rest as
  "and N other views".
- `cluster-by` — captions vs ML labels vs both.

### Narrative

- `narrative-detail` — `short` / `normal` / `long`, independent of photo count.
- `trip-km` / `trip-continue-km` — what counts as "away" and "same trip".
  `[hardcoded]`
- `pronoun` — third person ("they") vs first person ("we").
- `owner-display-name` — the name used in prose (distinct from `owner-name`,
  which only hides a name).
- `locale` — month/weekday language.

### Output & formatting

- `title` — journal title.
- `theme` — HTML light/dark/auto.
- `time-format` — 12-hour vs 24-hour.
- `date-format` — e.g. "Monday, January 1".
- `thumb-quality` / `thumb-format` — preview size/quality.

### Operations

- `jobs` — parallel workers for preview generation.
- `cache-dir` — where `out/photos/` previews live.
- `verbose` / `log-level`.
