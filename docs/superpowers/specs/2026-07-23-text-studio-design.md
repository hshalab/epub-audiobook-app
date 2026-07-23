# Text Studio & Remote Rendering v2 — Design Spec

## Overview

Two-phase feature set centered on a **clean text** contract:
1. **Phase 1 — Text Studio**: Edit patch text, search/replace, spell check, effect markers.
2. **Phase 2 — Remote Rendering v2**: Move chunk splitting to Kaggle/Colab, fingerprint-based resume.

## Core Concept: Patch Clean Text

`patch.clean_text` is the single source of truth for TTS input.

- **Created** from `build_patch_text()` (chapters → normalize → replace rules) on first edit.
- **Edited** by the user in Text Studio.
- **Consumed** by worker and export — `build_patch_text()` checks for `clean_text` first, falls back to derived text if NULL.

This means editing a patch does NOT affect chapters or other patches.

## Data Model Changes

### `patch` table additions

| Column | Type | Default | Purpose |
|--------|------|---------|---------|
| `clean_text` | TEXT | NULL | User-edited clean text; NULL = use derived |
| `clean_text_hash` | TEXT | NULL | SHA-256 of clean_text for change detection |
| `text_fingerprint` | TEXT | NULL | Hash of normalization + rules + chapter text for detecting stale clean_text |

### `patch_warning` table (new)

```sql
CREATE TABLE IF NOT EXISTS patch_warning (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    patch_id    INTEGER NOT NULL REFERENCES patch(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,  -- spell_vi | spell_en | junk | effect_marker
    position    INTEGER NOT NULL,
    length      INTEGER NOT NULL,
    original    TEXT NOT NULL,
    suggestion  TEXT NOT NULL DEFAULT '',
    accepted    INTEGER NOT NULL DEFAULT 0,  -- 0=pending, 1=accepted, 2=dismissed
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_patch_warning_patch ON patch_warning(patch_id, kind);
```

### `sound_effect` table (new)

```sql
CREATE TABLE IF NOT EXISTS sound_effect (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id     INTEGER NOT NULL REFERENCES book(id) ON DELETE CASCADE,
    marker      TEXT NOT NULL,      -- e.g. [tiếng khóc]
    file_path   TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sound_effect_book ON sound_effect(book_id);
```

## Text Analysis Module (`app/text_analysis.py`)

Analyzes text and returns warnings. Stateless — runs on demand.

### Warning types

| Kind | Detection | Suggestion |
|------|-----------|------------|
| `spell_vi` | Pattern: Vietnamese words with suspicious char sequences | Top 1-3 candidates from simple edit-distance |
| `spell_en` | Words matching `[a-zA-Z]+` not in English word list | Top candidates |
| `junk` | Chars like `@@`, `##`, `**`, CJK outside markers | Removal suggestion |
| `effect_marker` | `[tiếng khóc]`, `[tiếng rên]`, `[tiếng hét]`, etc. | Keep as marker or replace |

### Approach (ponytail)

- No external spell-check library in Phase 1. Use regex patterns + word lists.
- Vietnamese: detect doubled vowels, impossible consonant clusters, mixed diacritics.
- English: simple word list from stdlib or bundled top-5k words.
- Effect markers: regex `\[(tiếng\s+\w+|âm\s+thanh\s+\w+|[a-z]+\s*(cry|scream|moan|laugh|sigh))\]`

## API Routes (`/books/{book_id}/text-studio/...`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/text-studio` | Text Studio page |
| GET | `/text-studio/patches/{patch_id}` | Get clean_text (or derived) as JSON |
| PUT | `/text-studio/patches/{patch_id}` | Save edited clean_text |
| POST | `/text-studio/patches/{patch_id}/analyze` | Run text analysis, return warnings |
| POST | `/text-studio/patches/{patch_id}/apply-warning` | Accept/dismiss a warning |
| POST | `/text-studio/patches/{patch_id}/replace` | Search/replace within clean_text |
| POST | `/text-studio/patches/{patch_id}/reset` | Revert clean_text to derived |

## UI Layout

### Desktop (≥960px): Three columns

```
┌─────────────┬──────────────────────────┬───────────────┐
│ PATCH LIST  │    EDITOR (textarea)     │   WARNINGS    │
│             │                          │               │
│  01 ✓       │  [Search] [Replace]      │  Spell (3)    │
│  02 ●       │                          │  Junk (2)     │
│  03         │  Trời tối dần. Cô ấy    │  Effects (1)  │
│  04         │  khóc nức nở bên cửa    │               │
│             │  sổ.                     │  ▸ khóc nức   │
│             │  [tiếng khóc] "Đừng..."  │    nở → ...   │
│             │                          │               │
│             │  [Lưu] [Phân tích] [↩]  │               │
└─────────────┴──────────────────────────┴───────────────┘
```

### Mobile (<960px): Single column with drawer

```
┌────────────────────────────┐
│ ◀ Patch 02/18 ▶  [⋮ Menu] │
├────────────────────────────┤
│ [Search] [Replace]         │
│                            │
│ Trời tối dần. Cô ấy       │
│ khóc nức nở bên cửa sổ.  │
│ [tiếng khóc] "Đừng..."   │
│                            │
│ [Lưu] [Phân tích] [↩]    │
├────────────────────────────┤
│ ▼ Warnings (6)             │
│   Spell (3) · Junk (2) · FX│
└────────────────────────────┘
```

## Integration Points

### Worker (`worker.py`)

Before TTS: `text = repository.get_effective_patch_text(conn, patch)`

### Export (`drive_export.py`)

Uses `get_effective_patch_text()` for chunk text files.

### Rule/Normalization changes

When rules or normalization settings change, existing `clean_text` becomes stale.
`text_fingerprint` detects this; UI shows "clean_text may be outdated" warning.

## Phase 2 Preview (not implemented here)

- Chunk splitting moves to notebook
- `text_fingerprint` used for resume
- Batch export includes clean_text instead of chunk files
- Notebook reads clean_text and splits internally
