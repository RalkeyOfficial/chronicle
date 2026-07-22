# Chronicle

A small desktop app for **organizing and watching a folder of videos** — built for
episodic content (a series, a course, a channel archive) where you care about the
*order* you watch things in.

Chronicle keeps two independent orderings of the same videos:

- **Chronological** — a hand-arranged story/watch order you build by dragging.
- **Upload order** — sorted automatically by each video's real upload date (read
  from the file's embedded metadata). No manual work.

It also tracks what you've watched, resumes where you left off, lets you add notes,
and opens each video in your normal media player. **It never modifies your video
files** — everything Chronicle knows lives in a single sidecar file next to them.

---

## Why it's robust: identity by content, not by path

Chronicle identifies every video by a **content fingerprint** (its size + a hash of
its first 1 MiB), *not* by its filename or location. This means you can freely
**move, rename, reorganize, or even flatten all your folders into one** and Chronicle
still recognizes each video and keeps its order, notes, and watched state.

All of that state is stored in **`chronicle_library.json`**, created in the folder you
run Chronicle on. Delete it and you start fresh; copy it alongside the videos and your
setup travels with them.

For **sharing** a setup with someone who has *different* copies of the same videos,
Chronicle also records each video's **YouTube ID** (parsed from the embedded source
link) as a secondary identity — it survives re-encoding, so a shared library can be
matched to files at another resolution or bitrate. See **Sharing a library** below.

---

## Sharing a library

Chronicle can hand your ordering and curation to someone else — or to your own second
machine — without touching either side's personal state.

- **Export library…** writes a shareable file containing both orders and the
  *non-personal* metadata: titles, seasons, side-story flags, source links, and date
  overrides. It deliberately **excludes** your notes, watched-marks, and resume
  positions.
- **Import library…** applies such a file to *your* copy of the videos. Each video is
  matched **by its YouTube ID first** — so it works even when your downloads differ in
  resolution, bitrate, or embedded subtitles — and falls back to matching **byte-identical
  files** for videos with no embedded link. You choose **Reset** (adopt the shared order)
  or **Merge** (append it to your own), exactly like *Re-import from folders*.

Your notes and watched-state are always kept. For videos in the shared file that you
**don't have** (no matching ID and no identical file), you pick what happens:

- **Keep as `[offline]` entries** (default) — they hold their place in the order as
  `[offline]` rows and **reconnect automatically** once you obtain a **byte-identical**
  copy of the file. (A *different* encode of the same video currently comes in as a fresh,
  unplaced entry — automatic reconnection across encodes is planned but not yet
  implemented.)
- **Skip** — they're left out entirely.

Either way, the import finishes with a **summary that lists exactly which videos weren't
in your copy**, so nothing happens silently.

> **Note:** cross-encode matching needs a YouTube-sourced video ID, which comes from the
> embedded link that yt-dlp writes. Videos with no link only match when the files are
> identical.

---

## Requirements

| Thing | Needed for | Install (Arch/CachyOS) | Install (Debian/Ubuntu) |
|-------|-----------|------------------------|-------------------------|
| **Python 3.9+** | running the app | `pacman -S python` | `apt install python3` |
| **PySide6** | the GUI | `pacman -S pyside6` | `pip install pyside6` |
| **A media player** — `mpv` (preferred), `vlc`, or `xdg-open` | playback | `pacman -S mpv` | `apt install mpv` |
| **ffmpeg** (provides `ffprobe`) — *optional* | auto-reading upload dates & links | `pacman -S ffmpeg` | `apt install ffmpeg` |

Chronicle still runs without `ffprobe` — you just won't get automatic upload dates
(you can enter them by hand). `mpv` is recommended because Chronicle asks it to
remember your exact position in each video (true resume).

---

## Recommended: download videos with yt-dlp

For **full intended support**, download your videos with
[**yt-dlp**](https://github.com/yt-dlp/yt-dlp) with metadata embedding enabled.

Chronicle's automatic features rely on metadata stored *inside* the video files:

- the **Upload order** view reads each file's embedded upload **`DATE`** tag,
- the **Open on YouTube** button reads the embedded source **URL** tag.

yt-dlp writes exactly these tags, so a collection built with it works out of the box.
For an easy, personalized yt-dlp setup, see:

> **<https://ralkeyofficial.github.io/knowledge-base/docs/media-downloading/yt-dlp-opinionated-setup>**

Videos from other sources still work — Chronicle will just have no dates to sort the
Upload view by (enter them by hand with **Override date**) and the YouTube button
stays disabled.

---

## Getting started

1. Put `chronicle.py` **in (or anywhere above) the folder of videos** you want to
   manage. Currently only `.mkv` files are discovered.
2. Run it:
   ```
   python3 chronicle.py            # manages the folder the script is in
   python3 chronicle.py /path/to/videos   # or point it at a folder
   ```
3. On first launch Chronicle will:
   - scan for videos and fingerprint them,
   - read each file's embedded **upload date** and **source URL** via `ffprobe`
     (a one-time progress bar; cached afterwards),
   - optionally seed the chronological order if your folders follow the supported
     naming convention (see **[FOLDER-STRUCTURE.md](FOLDER-STRUCTURE.md)**).

That's it. From then on the folder names don't matter — you organize everything in
the app.

Click **?  Help** inside the app at any time for an in-window explanation.

---

## The two views

Switch between them with the **View** dropdown at the top left.

### Chronological (manual)
Your story/watch order, built by hand.

- **Drag any row** up or down to reorder — saved instantly.
- Videos not placed yet appear in the right-hand box (**"Not in this order yet"**).
  Select one and press **← Add to order**, then drag it into position.
- **Remove from list →** takes a video *out of this order only*. It reappears on the
  right; nothing is deleted, and its notes and its place in the Upload order are
  untouched.
- You can interleave side-stories into the middle of a season, etc. — it's entirely
  up to you.

### Upload order (automatic)
Sorted for you by each video's real upload date; the date is shown on every row.

- **No dragging needed** — it sorts itself.
- If two videos share the same date (day precision), Chronicle can't know their true
  order, so you may **drag videos that share a date** to fine-tune them. Dragging
  across *different* dates snaps back — the date always wins.
- Videos with **no upload date** can't be sorted, so they appear in the right-hand
  box (**"No upload date yet"**). Give one a date (see below) to place it.

---

## Details panel (per video)

Select any video to edit it. Changes save automatically.

- **Title** — display name (does not rename the file).
- **Season** — a `[S#]` badge for grouping. Purely a label; doesn't affect ordering.
- **Side story / special** — adds a `+` to the badge (e.g. `[S3+]`) so you can spot
  specials. Doesn't move or hide anything.
- **Watched** — the `✓` on the row.
- **File upload date** — what Chronicle read from the file (read-only).
- **Override date** — tick this and pick a date to sort the video by *your* date
  instead of the file's (fix a wrong embedded date, or move a video to another day).
  An overridden date shows a `*` on the row. Untick to go back to the file's date.
- **Open on YouTube** — opens the video's source link in your browser (read from the
  file, when present).
- **Notes** — free text. Press **Save notes** (other fields save on their own).

**Row format:** `✓ [S3+] 2016-05-01* Title`
→ watched · Season 3 · side-story · sort date 2016-05-01 (overridden) · title.
`[offline]` means the file isn't found on disk right now — its order and notes are
kept until it returns.

---

## Toolbar buttons

- **▶ Continue watching** — jumps to and plays the last video you opened in the
  current order (or the first unwatched one).
- **Re-import from folders…** — re-reads the supported folder naming convention to
  rebuild the *chronological* order. Rarely needed. "Reset" overwrites the order;
  "Merge" just appends new videos. Your notes and watched marks are always kept.
- **Refresh dates from files** — re-reads every file's upload date and link. Use it
  if you re-downloaded files. Your manual date overrides are kept.
- **Export library…** / **Import library…** — share your ordering and curation, or apply
  someone else's, without touching either side's notes or watched-marks. See
  **[Sharing a library](#sharing-a-library)**.
- **?  Help** — the in-app guide.

---

## Where your data lives

- **`chronicle_library.json`** — all orders, notes, watched flags, dates, overrides,
  and a fingerprint cache. Written atomically; if it's ever corrupted, Chronicle
  backs it up to `chronicle_library.json.bak` and starts fresh rather than crashing.
- **`.mpvwatchlater/`** (only if you use mpv) — where mpv stores exact resume
  positions.

To move your library to a new machine, copy the videos **and** `chronicle_library.json`.

---

## Playback notes

- Playback launches an **external player** (mpv → vlc → `xdg-open`, whichever is
  found first). This is why `.mkv` "just works" — no codec setup inside the app.
- With **mpv**, Chronicle passes `--save-position-on-quit`, so quitting mid-episode
  and reopening it resumes exactly where you were.

---

## Troubleshooting

- **"No player found"** — install `mpv` (or `vlc`). See Requirements.
- **Upload dates are all empty / the button is disabled** — `ffprobe` isn't
  installed. Install `ffmpeg`, then click **Refresh dates from files**. Or set dates
  by hand with **Override date**.
- **A video shows `[offline]`** — the file isn't at its last-known path. Chronicle
  finds videos by content, so just make sure the folder is mounted/present and
  re-launch; it'll reconnect automatically.
- **An upload date is wrong** — tick **Override date** in Details and set the correct
  one.
- **PySide6 import error on launch** — `pip install pyside6` (or `pacman -S pyside6`).

---

## Verifying the build

Chronicle ships with a self-test of its core logic (no GUI needed):

```
python3 chronicle.py --selftest
```

It checks folder-name parsing, ordering, date handling, fingerprint stability, and
store round-trips. All checks should print `PASS`.

---

## See also

- **[FOLDER-STRUCTURE.md](FOLDER-STRUCTURE.md)** — the *optional* folder naming
  convention Chronicle can auto-import a chronological order from. You don't need it,
  but it saves setup time for large collections.
