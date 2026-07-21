# Supported folder structure (optional)

Chronicle works with **any** folder layout — it finds videos by scanning and
identifies them by content, so how they're arranged on disk doesn't matter.

This document describes an **optional** naming convention. If your folders follow
it, Chronicle can **auto-seed the chronological order** (and each video's season /
side-story labels) on first run, instead of you arranging everything by hand. It's a
convenience for large collections, used once — after import you can rename, move, or
flatten the folders however you like and nothing breaks.

If you don't use this convention, just ignore this file: Chronicle scans your videos
regardless, and you build the chronological order in the app.

> **Note on upload order:** Chronicle derives the **Upload** view from each file's
> embedded upload date, *not* from folder names. The `(Upload #N)` tag is therefore
> **optional** — a folder is recognized with or without it. Keeping it is still
> **recommended** as human-readable documentation of the release sequence.

---

## The folder name pattern

Each season folder is named:

```
season <CODE> - <optional title> - (Upload #<N>)
```

Reading the parts left to right:

- **`season`** — the literal keyword that marks a folder as a season for the importer.
  A folder that doesn't start with `season ` is skipped (its videos still show up in the
  app, just unplaced).
- **`<CODE>`** — the structural identifier: which season, sub-season, and/or side-story
  this folder is. This is the only part Chronicle uses to build the order. Grammar below.
- **`<optional title>`** — free-text label for your own reference (e.g. `Origins`,
  `Detours`). Purely cosmetic: it may be omitted, and may itself contain ` - ` or other
  punctuation. Chronicle ignores it (episode titles come from the file names instead).
- **`(Upload #<N>)`** — records the position this folder held in the channel's **upload
  timeline** — i.e. it was the `N`-th thing uploaded (`#01` = first upload, `#02` = second,
  …). It's a documentation label only: **optional**, and the number does not drive
  ordering (the Upload view reads real dates from the files). The dash right before it is
  optional too. Recommended to keep so the release order is legible from the folder names.

### The `<CODE>` grammar

The code answers three questions: *which season*, *which part of it* (sub-season), and
*is it a side-story*.

Notation: `NN` = a two-digit number (e.g. `01`, `00`, `12`). `<letter>` = a single
lowercase letter (`a`, `b`, `c`, …).

| Pattern | Meaning | Example code |
|---------|---------|--------------|
| `NN` | main season | `01` |
| `NNxNN` | main season split into a **sub-season** | `00x03` |
| `NN<letter>` | main season with a **side-story** | `01b` |
| `NNxNN<letter>` | main season, sub-season, **and** side-story | `00x01a` |

- The first `NN` is the **season number**.
- `xNN` (if present) is the **sub-season** number — see below.
- A trailing `<letter>` marks a **side-story** (`a`, `b`, `c`… = 1st, 2nd, 3rd side-story).

#### What is a "sub-season"?

A **sub-season** is a self-contained block of episodes that lives *under* a season number
but functions like its own season — without being promoted to the next season number.

The classic case: a show releases **Season 1 in three parts**. Each part is its own arc
with its own beginning and end and feels like a standalone season, but the creator never
called any of them "Season 2" — officially they're all still Season 1. You'd encode them:

```
season 01x01 - Part 1 - (Upload #01)
season 01x02 - Part 2 - (Upload #04)
season 01x03 - Part 3 - (Upload #07)
```

All three share season number `1`, and the `x01` / `x02` / `x03` keep them in order and
distinct. The next *real* season then becomes `season 02`. (In this collection, the
`season 0` prequel material is split the same way: `00x01`, `00x02`, `00x03`.)

#### What is a "side-story"?

A **side-story** is a bonus or special that hangs off a season (or sub-season) but isn't
part of the main storyline — a holiday special, a spin-off mini-arc, a one-off. The
trailing letter attaches it to its parent: `03a` is the first side-story of season 3;
`00x01a` is the first side-story of season 0's first sub-season. In the app these get a
`+` on their badge (e.g. `[S3+]`).

### Within a season folder

Put the episode files directly inside the folder and prefix each with a zero-padded
index so they sort correctly:

```
season 02 - (Upload #04)/
    01 - The Arrival.mkv
    02 - Settling In.mkv
    03 - Trouble.mkv
```

Chronicle orders episodes within a folder by filename, so the leading `NN - ` prefix
is what gives you a reliable episode order. (Only the leading number is required; the
rest of the name is free.)

---

## Worked examples

| Folder name | Season | Sub-season | Side-story |
|-------------|:------:|:----------:|:----------:|
| `season 01 - (Upload #02)` | 1 | — | — |
| `season 02a - Detours - (Upload #05)` | 2 | — | a (1st) |
| `season 00x01 - Origins - (Upload #03)` | 0 | 1 | — |
| `season 00x01a - Origins - Side Tale - (Upload #24)` | 0 | 1 | a (1st) |
| `season 00x03a - Side Arc - The Move (Upload #01)` | 0 | 3 | a (1st) |
| `season 06b - side story - D&D - (Upload #22)` | 6 | — | b (2nd) |

Note the last two have **no dash** before `(Upload …)` and a title containing dashes
or `&` — both are handled fine.

---

## How Chronicle turns this into a chronological order

On import, Chronicle sorts the folders by **(season, sub-season, side-story letter)**:

1. season number (ascending),
2. then sub-season number (folders without one come first),
3. then side-story letter (a folder with **no** letter comes **before** its lettered
   side-stories).

So a main season lands **before** its own side-stories. Within each folder, episodes
follow their `NN - ` filename order. The result is a sensible starting sequence like:

```
00x01 → 00x01a → 00x01b → 00x02 → 00x02a → 00x03 → 00x03a →
01 → 01a → 02 → 02a → 02b → 03 → 03a … 03f → 04 → 04a → 05 → 06 → 06a → 06b → 06c
```

Chronicle also sets each video's **season number** and **side-story flag** from the
code, which show up as the `[S#]` / `[S#+]` badges in the app.

### After import: fine-tune in the app

The auto-import is only a starting point. In real life a side-story often belongs
*in the middle* of a season rather than after it — so once imported, open the
**Chronological** view and **drag** those episodes into their true position. Your
manual order is saved and the folder names never need to change again.

---

## What is *not* required

- **File type / layout:** only `.mkv` files are discovered today, sitting directly
  inside their folder. (No convention is needed for the Upload view — that comes from
  embedded dates.)
- **The `(Upload #N)` tag:** optional. Folders are recognized with or without it; it's
  recommended only as a human-readable note of the release sequence.
- **Keeping this structure:** once imported, reorganize freely. Chronicle tracks each
  video by content, so moving, renaming, or flattening folders keeps everything intact.
- **Perfect names:** folders that don't match the pattern are simply skipped by the
  importer — their videos still appear in Chronicle, just unplaced, ready for you to
  add to the order in the app.
