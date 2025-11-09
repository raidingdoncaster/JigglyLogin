# Geocache Quest Story Guide

The quest story graph is stored in `data/geocache_story.json`. Technical assets (GPS targets, codes, NFC tags) live in `data/geocache_assets.json`. Update those two files together whenever you add, edit, or review content.

## File Layout

```json
{
  "version": 1,
  "title": "Whispers of the Wild Court",
  "acts": [...],
  "scenes": {...},
  "metadata": {...}
}
```

- **`acts`** – ordered list describing the main flow. Each act includes:
  - `id` – lowercase key (e.g. `act2`)
  - `title`, `intro`, and an `objectives` array
  - `scenes` – list of scene IDs in suggested order
  - `next_act` – scene ID triggered when the act finishes (can be `null`)
- **`scenes`** – dictionary keyed by scene ID. These are the blocks rendered by the front-end. Each scene can be one of:
  - `narration` – characters speaking text. Optional `cta` to jump to another scene.
  - `registration` – same as narration but expects form fields (used only in Act I).
  - `minigame` – wraps the gameplay interactions (artifact scans, riddles, GPS check-in, etc.).
  - Scenes can include `choices` instead of `cta` if there are multiple branches.
- **`metadata`** – freeform info (currently a description and a last-updated timestamp). Update this when you make notable changes.

## Location Reference

| Scene ID              | Location ID         | Latitude  | Longitude  | Radius (m) |
|-----------------------|---------------------|-----------|------------|------------|
| `act2.minster-location` | `doncaster_minster`   | 53.52220  | -1.13235   | 50         |
| `act2.mansion-location` | `mansion_house_trail` | 53.52271  | -1.13552   | 50         |
| `act2.lovers-location`  | `lovers_statue`       | 53.52127  | -1.12115   | 50         |
| `act3.pink-bike-location` | `pink_bike_stage`   | 53.52189  | -1.13312   | 50         |
| `act3.sir-nigel-location` | `sir_nigel_square`  | 53.52292  | -1.14108   | 50         |
| `act6.market-return`     | `market_hub`         | 53.52205  | -1.13340   | 50         |

Edit the latitude/longitude here in `data/geocache_assets.json`; the server will merge them into the story automatically.

## Artifact Reference

| Scene ID               | Artifact Slug | Code | NFC UID |
|------------------------|---------------|------|---------|
| `act1.compass-hunt`    | `compass-cracked` | 1729 | _TBD_ |
| `act2.sigil-dawn`      | `sigil-dawn`      | 3141 | _TBD_ |
| `act2.boardgame-sigil` | `sigil-roots`     | 2718 | _TBD_ |
| `act3.sigil-might`     | `sigil-might`     | 1618 | _TBD_ |

Codes and NFC UIDs live in `data/geocache_assets.json`. Update the table (and file) when props change; the backend injects the values into the quest runtime automatically.

## Minigame Types

Each minigame scene has a `minigame` object with at least a `kind` and `success_flag`. These flags are stored in `geocache_sessions.progress_flags` and drive progression gates in `geocache/services.py`.

| Kind            | Purpose / Fields                                                                                           |
|-----------------|-------------------------------------------------------------------------------------------------------------|
| `artifact_scan` | NFC or code interaction. Provide `artifact_slug`, optional `code_hint`.                                     |
| `location`      | GPS check-in. Provide `location_id`, `latitude`, `longitude`, optional `radius_m` (metres, defaults to 75).|
| `riddle`        | Multiple choice. Set `choices` array with `id`, `label`, and `correct` (boolean). Optional `failure_message`.|
| `focus`         | Tap-to-react mini-game. Set `orbs` (default 5) and `window_ms` (timeout per orb).                           |
| `illusion`      | Eldarni battle variant. Set `orbs`, `timeout_ms`, and optional `lines` array for taunts.                    |
| `mosaic`        | Puzzle placeholder. Only needs `puzzle_id`/`success_flag`.                                                  |
| `quiz`          | Multi-question form. Provide `questions` array with `id`, `prompt`, and `options`.                          |
| `combat`        | Final battle. Provide `rounds`, `symbols` array, optional `lines`.                                          |
| `ending`        | Final choice. Provide `options` array (`id`, `label`, optional `ending_id`, optional `epilogue`).           |

When you introduce a new `kind`, update `static/geocache/app.js` and `geocache/services.py` to understand it.

### Location fields

Every location minigame should include:

```json
"minigame": {
  "kind": "location",
  "location_id": "pink_bike_stage",
  "latitude": 53.52189,
  "longitude": -1.13312,
  "radius_m": 60,
  "prompt": "Tap check-in when you arrive at the Big Pink Bike stage."
}
```

- The backend enforces the `radius_m` using the provided coordinates (haversine distance). Players outside the radius receive a “location_out_of_range” error.
- A small radius keeps the experience honest; 40–75 m works well for city landmarks.
- Coordinates can be grabbed from Google Maps (right-click → “What’s here?”) and copied to 5 decimal places.
- If you do not supply coordinates, the radius fallback is 75 m and the server will not be able to enforce proximity, so try to avoid leaving them blank.

## Editing Workflow

1. **Duplicate the file** (optional) for backup: `cp data/geocache_story.json data/geocache_story.backup.json`.
2. **Edit content** in your IDE. Keep IDs lowercase with hyphens for readability.
3. **Validate JSON** after editing:
   ```bash
   python -m json.tool data/geocache_story.json
   ```
   This fails fast if commas or brackets are missing.
4. **Run spell/grammar passes** if needed—story text is player-facing.
5. **Update metadata** in the file (e.g. bump `metadata.last_updated`).
6. **Test locally**: start the Flask app with `USE_GEOCACHE_QUEST=1` and step through the acts you touched.
7. **Commit** the updated JSON along with any Supabase data seeding notes in the README.

## Progress Flags & Act Gates

Progression is enforced inside `geocache/services.py` via the `REQUIRED_FLAGS_BY_ACT` dictionary. Every `success_flag` you introduce should either appear in that dictionary (if the act requires it to advance) or be purely cosmetic.

Examples:

- Act II requires: `miners_riddle_solved`, `sigil_dawn_recovered`, `focus_test_passed`, `sigil_roots_recovered`, `oracle_mood_profiled`.
- Act VI requires: `market_returned`, `pink_bike_check`, `illusion_battle_won`, `sir_nigel_check_in`, `sigil_might_recovered`, `order_defeated`.

Whenever you add a new gate, update both `REQUIRED_FLAGS_BY_ACT` and the front-end `REQUIRED_FLAGS` map in `static/geocache/app.js`.

## Admin Story Viewer

Visit `/admin/geocache/story` (link available on the admin dashboard) to see a human-readable listing of all acts and scenes. This is useful for quick content reviews without opening JSON manually.

## Supabase Seeds

After editing, cross-check that any new `artifact_slug` or `location_id` has a matching row in Supabase. The reference list lives in the “Act II / Act III / Act VI Data Seeds” section of `README_GEOCACHE_SUPABASE.md`.

## Validation Script (Optional)

Run the story inspection tool:

```bash
python -m geocache.story_tool summary
```

This prints acts, required flags, and scene coverage to help spot missing IDs or duplicates.

---

Need a custom narrative component that doesn’t fit the current schema? Sketch it in this guide first, then extend both the story JSON and the supporting Python/JS modules so everything stays in sync.
