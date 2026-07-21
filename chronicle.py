#!/usr/bin/env python3
"""
Chronicle
=========

A launcher/organizer for a folder of videos (e.g. the episodes of a series).

Chronicle makes the folder layout irrelevant. It identifies each video by a
*content fingerprint* (size + hash of the first 1 MiB), so metadata sticks to
the video no matter how you move, rename, or flatten the folders. All ordering,
notes and watched-state live in a single JSON store next to this script.

Two orders are maintained:
  • Chronological — a hand-editable, drag-to-reorder story sequence.
  • Upload order  — derived automatically from each file's embedded upload
                    date (read via ffprobe); episodes sharing a date can be
                    nudged by hand, and a per-video date override is available.

As an optional bootstrap, folders named like "season <code> - <title> -
(Upload #<n>)" are parsed once to seed the chronological order; on collections
that don't use that layout this simply finds nothing and you build the order in
the GUI. After that the folder names no longer matter.

The app never modifies your video files. Playback launches an external player
(mpv preferred, then vlc, then xdg-open).

Usage:
    python3 chronicle.py [ROOT]     # ROOT defaults to this file's folder
    python3 chronicle.py --selftest # run pure-logic unit checks
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------- #
#  Section 1: pure helpers (no Qt) -- unit-tested by --selftest                #
# --------------------------------------------------------------------------- #

STORE_NAME = "chronicle_library.json"
SCHEMA_VERSION = 2
CHUNK = 1024 * 1024  # 1 MiB fingerprint chunk

SEASON_RE = re.compile(
    r"^season (?P<code>\S+)\s*-?\s*(?P<title>.*?)\s*-?\s*\(Upload #(?P<upload>\d+)\)$"
)
CODE_RE = re.compile(r"^(\d+)(?:x(\d+))?([a-z]*)$")
EP_PREFIX_RE = re.compile(r"^\d+\s*-\s*")
DATE8_RE = re.compile(r"(\d{4})(\d{2})(\d{2})")  # YYYYMMDD anywhere in the tag

# Fullwidth / typographic substitutes yt-dlp uses for filesystem-illegal chars.
FULLWIDTH = {
    "｜": "|", "：": ":", "’": "'", "＂": '"', "？": "?",
    "＊": "*", "／": "/", "＼": "\\", "＜": "<", "＞": ">",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fingerprint(path: Path) -> str:
    """Content identity: file size + blake2b of the first 1 MiB.

    Survives moving and renaming; only changes if the file content changes.
    """
    st = path.stat()
    h = hashlib.blake2b(digest_size=16)
    with open(path, "rb") as f:
        h.update(f.read(CHUNK))
    return f"{st.st_size}:{h.hexdigest()}"


def code_sort_key(code: str) -> tuple:
    """Sort key that turns season codes into true chronological order.

    (season, subseason, side-letter). Empty letter sorts before 'a', so a main
    season precedes its side-stories. Unparseable codes sort to the very end.
    """
    m = CODE_RE.match(code)
    if not m:
        return (9999, 9999, code)
    return (int(m.group(1)), int(m.group(2) or 0), m.group(3) or "")


def clean_title(filename: str) -> str:
    """Turn a raw .mkv filename into a display title."""
    name = filename
    if name.lower().endswith(".mkv"):
        name = name[:-4]
    name = EP_PREFIX_RE.sub("", name)  # strip leading "NN - "
    for bad, good in FULLWIDTH.items():
        name = name.replace(bad, good)
    return name.strip() or filename


def parse_season_folder(folder: str):
    """Return (code, upload:int, season:int|None, side_story:bool) or None."""
    m = SEASON_RE.match(folder)
    if not m:
        return None
    code = m.group("code")
    upload = int(m.group("upload"))
    cm = CODE_RE.match(code)
    season = int(cm.group(1)) if cm else None
    side_story = bool(cm.group(3)) if cm else False
    return code, upload, season, side_story


HAVE_FFPROBE = bool(shutil.which("ffprobe"))


def _date8_to_iso(value: str | None) -> str | None:
    """'20151123' (possibly with extra chars) -> '2015-11-23'; else None."""
    if not value:
        return None
    m = DATE8_RE.search(value)
    if not m:
        return None
    y, mo, d = m.group(1), m.group(2), m.group(3)
    if not ("1900" <= y <= "2100" and "01" <= mo <= "12" and "01" <= d <= "31"):
        return None
    return f"{y}-{mo}-{d}"


def read_embedded_meta(path: Path) -> dict:
    """Read upload date + source URL from a file's embedded tags via ffprobe.

    Returns {"upload_date": "YYYY-MM-DD"|None, "url": str|None}. Never raises;
    if ffprobe is missing or the file has no tags, both are None so the app
    still works and the user can fill the date in by hand.
    """
    result = {"upload_date": None, "url": None}
    if not HAVE_FFPROBE:
        return result
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries",
             "format_tags=DATE,PURL,COMMENT", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30,
        ).stdout
        tags = (json.loads(out).get("format", {}) or {}).get("tags", {}) or {}
    except (OSError, ValueError, subprocess.SubprocessError):
        return result
    # ffprobe may vary tag case; look them up case-insensitively.
    lower = {k.lower(): v for k, v in tags.items()}
    result["upload_date"] = _date8_to_iso(lower.get("date"))
    url = lower.get("purl") or lower.get("comment")
    if url and str(url).startswith("http"):
        result["url"] = str(url).strip()
    return result


# --------------------------------------------------------------------------- #
#  Section 2-5: Library model + store + scanner + importer                     #
# --------------------------------------------------------------------------- #


def default_video(filename: str) -> dict:
    return {
        "title": clean_title(filename),
        "season": None,
        "side_story": False,
        "orig_folder": None,
        "note": "",
        "watched": False,
        "resume_seconds": 0,
        "last_opened": None,
        "upload_date": None,           # ISO "YYYY-MM-DD" read from the file
        "upload_date_override": None,  # ISO date the user sets to relocate it
        "url": None,                   # source URL read from the file
        "meta_probed": False,          # have we tried ffprobe on this file yet
    }


def effective_date(v: dict) -> str | None:
    """The date an episode sorts by: the user's override, else the file's date."""
    return v.get("upload_date_override") or v.get("upload_date")


class Library:
    """In-memory model backed by the JSON store.

    Scan is authoritative for a video's existence/current path; the store is
    authoritative for identity metadata and ordering.
    """

    def __init__(self, root: Path):
        self.root = root
        self.store_path = root / STORE_NAME
        self.chrono_order: list[str] = []
        self.upload_order: list[str] = []
        self.videos: dict[str, dict] = {}
        self.fp_cache: dict[str, dict] = {}
        self.position: dict[str, str | None] = {"chrono": None, "upload": None}
        self.last_order: str = "chrono"
        self.path_by_fp: dict[str, Path] = {}  # transient: online videos only
        self.schema_loaded: int = SCHEMA_VERSION  # fresh store = current schema

    # ---- persistence ----------------------------------------------------- #
    def load(self) -> None:
        if not self.store_path.exists():
            return
        try:
            data = json.loads(self.store_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeError):
            # Corrupt store: back it up, then start empty rather than crash.
            try:
                self.store_path.replace(self.store_path.with_suffix(".json.bak"))
            except OSError:
                pass
            return
        self.chrono_order = list(data.get("chrono_order", []))
        self.upload_order = list(data.get("upload_order", []))
        self.videos = dict(data.get("videos", {}))
        self.fp_cache = dict(data.get("fp_cache", {}))
        pos = data.get("position", {})
        self.position = {"chrono": pos.get("chrono"), "upload": pos.get("upload")}
        self.last_order = data.get("last_order", "chrono")
        self.schema_loaded = int(data.get("schema_version", 1))

    def save(self) -> None:
        data = {
            "schema_version": SCHEMA_VERSION,
            "app": "chronicle",
            "root": str(self.root),
            "last_scan": now_iso(),
            "chrono_order": self.chrono_order,
            "upload_order": self.upload_order,
            "videos": self.videos,
            "fp_cache": self.fp_cache,
            "position": self.position,
            "last_order": self.last_order,
        }
        tmp = self.store_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.store_path)

    # ---- scanning --------------------------------------------------------- #
    def scan(self) -> None:
        """Find every .mkv under root, fingerprint (with cache), refresh state."""
        self.path_by_fp = {}
        new_cache: dict[str, dict] = {}
        for path in sorted(self.root.rglob("*.mkv")):
            if not path.is_file():
                continue
            key = str(path)
            try:
                st = path.stat()
            except OSError:
                continue
            cached = self.fp_cache.get(key)
            if cached and cached.get("size") == st.st_size and cached.get("mtime") == st.st_mtime:
                fp = cached["fp"]
            else:
                try:
                    fp = fingerprint(path)
                except OSError:
                    continue
            new_cache[key] = {"size": st.st_size, "mtime": st.st_mtime, "fp": fp}
            self.path_by_fp[fp] = path
            # Ensure every discovered video has a metadata entry.
            if fp not in self.videos:
                self.videos[fp] = default_video(path.name)
        self.fp_cache = new_cache

    # ---- one-time bootstrap import --------------------------------------- #
    def import_from_folders(self, reset_orders: bool = True) -> dict:
        """Seed metadata + both orders from the current 'season ...' folders.

        Returns a small summary dict for the caller to report.
        """
        # Group scanned files by their top-level folder under root.
        folders: dict[str, dict] = {}
        for fp, path in self.path_by_fp.items():
            try:
                rel = path.relative_to(self.root)
            except ValueError:
                continue
            if len(rel.parts) < 2:
                continue  # sits directly in root -> not a season folder
            folder = rel.parts[0]
            parsed = parse_season_folder(folder)
            if not parsed:
                continue
            code, upload, season, side_story = parsed
            entry = folders.setdefault(
                folder, {"code": code, "upload": upload, "files": []}
            )
            entry["files"].append((path.name, fp))
            # Seed per-video metadata (only overwrite structural fields).
            v = self.videos[fp]
            v["season"] = season
            v["side_story"] = side_story
            v["orig_folder"] = folder
            if not v.get("title"):
                v["title"] = clean_title(path.name)

        # Chronological order is a hand-editable story sequence seeded from the
        # folder codes. Upload order is NOT seeded here — it is derived from
        # embedded dates once those are read (see rebuild_upload_backbone).
        chrono = []
        for folder in sorted(folders, key=lambda f: code_sort_key(folders[f]["code"])):
            for _name, fp in sorted(folders[folder]["files"]):
                chrono.append(fp)

        if reset_orders:
            self.chrono_order = chrono
        else:
            for fp in chrono:  # merge: keep existing order, append new fps
                if fp not in self.chrono_order:
                    self.chrono_order.append(fp)
        return {"folders": len(folders), "videos": len(chrono)}

    # ---- embedded metadata (upload date + url) --------------------------- #
    def videos_missing_meta(self) -> list[str]:
        """Online videos whose embedded metadata hasn't been read yet."""
        return [fp for fp in self.path_by_fp
                if not self.videos.get(fp, {}).get("meta_probed")]

    def apply_meta(self, fp: str, meta: dict) -> None:
        """Store ffprobe results. Never touches the user's date override."""
        v = self.videos.setdefault(fp, default_video(""))
        v["upload_date"] = meta.get("upload_date")
        v["url"] = meta.get("url")
        v["meta_probed"] = True

    def clear_meta_probed(self) -> None:
        """Force the next scan to re-read every file's metadata."""
        for v in self.videos.values():
            v["meta_probed"] = False

    def rebuild_upload_backbone(self) -> None:
        """(Re)seed the same-day tiebreak list from effective dates.

        Ordered by (effective date, chronological position) so same-day
        episodes start out in story order; the user can then drag to nudge.
        """
        cidx = {fp: i for i, fp in enumerate(self.chrono_order)}
        dated = [fp for fp in self.videos if effective_date(self.videos[fp])]
        dated.sort(key=lambda fp: (effective_date(self.videos[fp]),
                                   cidx.get(fp, 10 ** 9), fp))
        self.upload_order = dated

    def migrate_if_needed(self) -> bool:
        """After metadata is available, build the upload backbone once.

        Runs for pre-date stores (schema < 2) or an empty backbone. Existing
        within-day nudges are preserved on later launches.
        """
        if self.schema_loaded < 2 or not self.upload_order:
            self.rebuild_upload_backbone()
            self.schema_loaded = SCHEMA_VERSION
            return True
        return False

    # ---- ordering queries ------------------------------------------------- #
    def order(self, name: str) -> list[str]:
        """The rendered order.

        chrono: the hand-editable list as-is.
        upload: every video WITH an effective date, sorted by
        (date, tiebreak-position). Undated videos are excluded (they surface
        via unsorted()).
        """
        if name == "chrono":
            return list(self.chrono_order)
        tb = {fp: i for i, fp in enumerate(self.upload_order)}
        dated = [fp for fp in self.videos if effective_date(self.videos[fp])]
        dated.sort(key=lambda fp: (effective_date(self.videos[fp]),
                                   tb.get(fp, 10 ** 9), fp))
        return dated

    def set_order(self, name: str, fps: list[str]) -> None:
        if name == "chrono":
            self.chrono_order = fps
        else:
            # The visual (date-sorted) list after a drag becomes the new
            # tiebreak backbone. Since order() re-sorts by date first, this only
            # ever changes ordering WITHIN a shared date — cross-date drags snap
            # back on the next render, which is the intended behavior.
            self.upload_order = list(fps)

    def is_online(self, fp: str) -> bool:
        return fp in self.path_by_fp

    def unsorted(self, name: str) -> list[str]:
        """Online videos that don't appear in the given order.

        chrono: not placed in the chronological list.
        upload: no effective date yet (nothing to sort them by).
        """
        if name == "chrono":
            in_order = set(self.chrono_order)
            return [fp for fp in self.path_by_fp if fp not in in_order]
        return [fp for fp in self.path_by_fp
                if not effective_date(self.videos.get(fp, {}))]

    def first_unwatched(self, name: str) -> str | None:
        for fp in self.order(name):
            if self.is_online(fp) and not self.videos.get(fp, {}).get("watched"):
                return fp
        return None


# --------------------------------------------------------------------------- #
#  Section 6: launcher                                                         #
# --------------------------------------------------------------------------- #


def resolve_player() -> tuple[str, str | None]:
    for name in ("mpv", "vlc"):
        exe = shutil.which(name)
        if exe:
            return name, exe
    return "xdg-open", shutil.which("xdg-open")


# --------------------------------------------------------------------------- #
#  Section 7: GUI (PySide6)                                                    #
# --------------------------------------------------------------------------- #


def run_gui(lib: Library) -> int:
    from PySide6.QtCore import Qt, QDate, QProcess, QTimer, QUrl, Signal
    from PySide6.QtGui import QColor, QDesktopServices
    from PySide6.QtWidgets import (
        QAbstractItemView, QApplication, QCheckBox, QComboBox, QDateEdit,
        QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
        QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit,
        QProgressDialog, QPushButton, QSpinBox, QSplitter, QTextBrowser,
        QVBoxLayout, QWidget,
    )

    FP_ROLE = Qt.UserRole
    player_name, player_exe = resolve_player()

    class OrderList(QListWidget):
        reordered = Signal()

        def __init__(self):
            super().__init__()
            self.setDragDropMode(QAbstractItemView.InternalMove)
            self.setSelectionMode(QAbstractItemView.ExtendedSelection)
            self.setDefaultDropAction(Qt.MoveAction)
            self.setUniformItemSizes(True)

        def dropEvent(self, e):
            super().dropEvent(e)
            self.reordered.emit()

    class Window(QMainWindow):
        def __init__(self):
            super().__init__()
            self.lib = lib
            self.current_order = lib.last_order if lib.last_order in ("chrono", "upload") else "chrono"
            self.setWindowTitle("Chronicle")
            self.resize(1080, 720)
            self._build()
            self._apply_mode()
            self.refresh_lists()
            # Fill in upload dates from the files shortly after the window paints.
            QTimer.singleShot(50, self._startup_metadata)

        def closeEvent(self, e):
            # Only the main window closing should end the app. (With
            # quitOnLastWindowClosed disabled below, dialogs closing can't.)
            helpwin = getattr(self, "_help_win", None)
            if helpwin is not None:
                helpwin.close()
            self.lib.save()
            super().closeEvent(e)
            QApplication.quit()

        # ---- construction ------------------------------------------------ #
        def _build(self):
            central = QWidget()
            self.setCentralWidget(central)
            outer = QVBoxLayout(central)

            # toolbar row
            bar = QHBoxLayout()
            self.order_combo = QComboBox()
            self.order_combo.addItem("Chronological order", "chrono")
            self.order_combo.addItem("Upload order", "upload")
            self.order_combo.setCurrentIndex(0 if self.current_order == "chrono" else 1)
            self.order_combo.currentIndexChanged.connect(self.on_order_changed)
            self.order_combo.setToolTip(
                "Which ordering to show and edit:\n"
                "• Chronological — the in-story timeline you arrange by hand.\n"
                "• Upload order — sorted automatically by each video's upload date.\n"
                "Both are independent; changing one never affects the other."
            )
            bar.addWidget(QLabel("View:"))
            bar.addWidget(self.order_combo)

            self.continue_btn = QPushButton("▶ Continue watching")
            self.continue_btn.clicked.connect(self.on_continue)
            self.continue_btn.setToolTip(
                "Jump to and play the last episode you opened in this order.\n"
                "If you've never played one, jumps to the first unwatched episode."
            )
            bar.addWidget(self.continue_btn)

            self.reimport_btn = QPushButton("Re-import from folders…")
            self.reimport_btn.clicked.connect(self.on_reimport)
            self.reimport_btn.setToolTip(
                "Re-read the original 'season … (Upload #N)' folder names to rebuild\n"
                "the ordering. Use only if you added new folders or want to start over.\n"
                "Your notes and watched-marks are always kept."
            )
            bar.addWidget(self.reimport_btn)

            self.refresh_dates_btn = QPushButton("Refresh dates from files")
            self.refresh_dates_btn.clicked.connect(self.on_refresh_dates)
            self.refresh_dates_btn.setToolTip(
                "Re-read every file's embedded upload date and YouTube link.\n"
                "Normally automatic; use this if you re-downloaded files.\n"
                "Your manual date overrides are kept."
            )
            self.refresh_dates_btn.setEnabled(HAVE_FFPROBE)
            if not HAVE_FFPROBE:
                self.refresh_dates_btn.setToolTip(
                    "ffprobe not found — install ffmpeg to auto-read upload dates."
                )
            bar.addWidget(self.refresh_dates_btn)

            self.help_btn = QPushButton("?  Help")
            self.help_btn.clicked.connect(self.on_help)
            self.help_btn.setToolTip("What everything in this window does.")
            bar.addWidget(self.help_btn)

            bar.addStretch(1)
            player_lbl = QLabel(f"Player: {player_name}" if player_exe else "Player: NONE FOUND")
            player_lbl.setToolTip("The external video player episodes open in when you press Play.")
            bar.addWidget(player_lbl)
            outer.addLayout(bar)

            # legend banner — explains the row format at a glance (updated per view)
            self.legend = QLabel()
            self.legend.setWordWrap(True)
            self.legend.setStyleSheet("color: gray; padding: 2px 0;")
            outer.addWidget(self.legend)

            # main splitter: ordered list | (unsorted + details)
            split = QSplitter(Qt.Horizontal)

            left = QWidget()
            lv = QVBoxLayout(left)
            lv.setContentsMargins(0, 0, 0, 0)
            self.order_label = QLabel()
            lv.addWidget(self.order_label)
            self.main_list = OrderList()
            self.main_list.reordered.connect(self.on_reordered)
            self.main_list.itemDoubleClicked.connect(lambda it: self.play(it.data(FP_ROLE)))
            self.main_list.currentItemChanged.connect(lambda cur, _prev: self.on_select(cur))
            self.main_list.setToolTip(
                "The playlist for the selected order.\n"
                "• Drag any row up or down to reorder — saved instantly.\n"
                "• Double-click a row to play it."
            )
            lv.addWidget(self.main_list, 1)
            row = QHBoxLayout()
            play_btn = QPushButton("▶ Play")
            play_btn.clicked.connect(lambda: self.play_selected(self.main_list))
            play_btn.setToolTip("Play the highlighted episode (same as double-clicking it).")
            watched_btn = QPushButton("Toggle watched")
            watched_btn.clicked.connect(self.toggle_watched)
            watched_btn.setToolTip("Mark the highlighted episode watched / unwatched (the ✓).")
            self.remove_btn = QPushButton("Remove from list →")
            self.remove_btn.clicked.connect(self.remove_from_order)
            self.remove_btn.setToolTip(
                "Take the highlighted episode out of THIS order only.\n"
                "It moves to the 'Not in this order' list on the right.\n"
                "Nothing is deleted — the video file, its notes and its place in\n"
                "the other order are all untouched."
            )
            row.addWidget(play_btn)
            row.addWidget(watched_btn)
            row.addWidget(self.remove_btn)
            lv.addLayout(row)
            split.addWidget(left)

            right = QWidget()
            rv = QVBoxLayout(right)
            rv.setContentsMargins(0, 0, 0, 0)
            self.unsorted_hdr = QLabel("Not in this order yet")
            self.unsorted_hdr.setToolTip(
                "Videos found on disk that haven't been placed into the current order.\n"
                "New downloads show up here, plus anything you removed from the list."
            )
            rv.addWidget(self.unsorted_hdr)
            self.unsorted_list = QListWidget()
            self.unsorted_list.itemDoubleClicked.connect(lambda it: self.play(it.data(FP_ROLE)))
            self.unsorted_list.currentItemChanged.connect(lambda cur, _prev: self.on_select(cur))
            rv.addWidget(self.unsorted_list, 1)
            self.add_btn = QPushButton("← Add to order")
            self.add_btn.clicked.connect(self.add_to_order)
            self.add_btn.setToolTip(
                "Put the highlighted episode into the current order (added to the bottom\n"
                "of the left list). Drag it from there to its correct position."
            )
            rv.addWidget(self.add_btn)

            # details / metadata editor
            details_hdr = QLabel("Details — the highlighted episode")
            details_hdr.setToolTip("Edit info for whichever episode is highlighted in either list. Saves automatically.")
            rv.addWidget(details_hdr)
            form = QFormLayout()
            self.title_edit = QLineEdit()
            self.title_edit.setToolTip("Display name for this episode. Renaming here does NOT touch the video file.")
            self.title_edit.editingFinished.connect(self.save_details)
            # Optional, open-ended season number. The checkbox is the "has a
            # season" switch (untick = no season); the spinbox holds the number
            # with no ceiling. Mirrors the "Override date" idiom below.
            self.season_chk = QCheckBox()
            self.season_chk.setToolTip(
                "Tick if this episode belongs to a numbered season; untick for none.\n"
                "Untick is the quickest way (Space) to clear the season."
            )
            self.season_chk.toggled.connect(self.on_season_toggled)
            self.season_edit = QSpinBox()
            self.season_edit.setRange(0, 999)
            self.season_edit.valueChanged.connect(self.save_details)
            self.season_edit.setToolTip(
                "Which season this episode belongs to. Shown as the [S#] badge on the row.\n"
                "Type or spin to any number — there is no upper limit.\n"
                "This is just a label for grouping — it does not affect the ordering."
            )
            season_row = QHBoxLayout()
            season_row.addWidget(self.season_chk)
            season_row.addWidget(self.season_edit, 1)
            self.side_edit = QCheckBox("Side story / special")
            self.side_edit.stateChanged.connect(self.save_details)
            self.side_edit.setToolTip(
                "Tick if this is a side-story or special rather than a main-storyline episode.\n"
                "It just adds a '+' to the season badge (e.g. [S3+]) so you can spot them.\n"
                "It does not move or hide the episode."
            )
            self.watched_edit = QCheckBox("Watched")
            self.watched_edit.stateChanged.connect(self.save_details)
            self.watched_edit.setToolTip("Whether you've seen this episode. Shown as the ✓ on the row.")

            # Upload date: file's date (read-only label) + optional override.
            self.filedate_lbl = QLabel("—")
            self.filedate_lbl.setToolTip("The upload date read from the video file's embedded metadata.")
            self.override_chk = QCheckBox("Override date")
            self.override_chk.setToolTip(
                "Sort this episode by a date YOU choose instead of the file's date.\n"
                "Use it to fix a wrong embedded date or move a video to another day.\n"
                "Only affects Upload order."
            )
            self.override_chk.toggled.connect(self.on_date_changed)
            self.date_edit = QDateEdit()
            self.date_edit.setCalendarPopup(True)
            self.date_edit.setDisplayFormat("yyyy-MM-dd")
            self.date_edit.setToolTip("The override date used to place this episode in Upload order.")
            self.date_edit.dateChanged.connect(self.on_date_changed)
            date_row = QHBoxLayout()
            date_row.addWidget(self.override_chk)
            date_row.addWidget(self.date_edit, 1)

            self.yt_btn = QPushButton("Open on YouTube")
            self.yt_btn.setToolTip("Open this episode's source video in your browser (URL read from the file).")
            self.yt_btn.clicked.connect(self.open_youtube)

            self.note_edit = QPlainTextEdit()
            self.note_edit.setFixedHeight(90)
            self.note_edit.setToolTip("Free-text notes for this episode. Click 'Save notes' (or edit another field) to save.")
            self.note_edit.setPlaceholderText("Your notes about this episode…")
            form.addRow("Title", self.title_edit)
            form.addRow("Season", season_row)
            form.addRow("", self.side_edit)
            form.addRow("", self.watched_edit)
            form.addRow("File upload date", self.filedate_lbl)
            form.addRow("Sort date", date_row)
            form.addRow("", self.yt_btn)
            rv.addLayout(form)
            rv.addWidget(QLabel("Notes"))
            rv.addWidget(self.note_edit)
            note_save = QPushButton("Save notes")
            note_save.clicked.connect(self.save_details)
            note_save.setToolTip("Save the notes above (fields other than notes save on their own).")
            rv.addWidget(note_save)
            split.addWidget(right)

            split.setStretchFactor(0, 3)
            split.setStretchFactor(1, 2)
            outer.addWidget(split, 1)

            self.status = self.statusBar()
            self._selected_fp: str | None = None
            self._loading_details = False

            if not player_exe:
                QMessageBox.warning(
                    None, "No player found",
                    "Could not find mpv, vlc, or xdg-open on PATH.\n"
                    "Install one (e.g. 'pacman -S mpv') to play videos.",
                )

        # ---- rendering ---------------------------------------------------- #
        def label_for(self, fp: str) -> str:
            v = self.lib.videos.get(fp, {})
            check = "✓ " if v.get("watched") else "   "
            s = v.get("season")
            badge = f"S{s}" if s is not None else "S?"
            if v.get("side_story"):
                badge += "+"
            offline = "" if self.lib.is_online(fp) else "  [offline]"
            date = ""
            if self.current_order == "upload":
                d = effective_date(v)
                if d:
                    star = "*" if v.get("upload_date_override") else ""
                    date = f"{d}{star}  "
            return f"{check}[{badge}] {date}{v.get('title', fp)}{offline}"

        def _make_item(self, fp: str) -> QListWidgetItem:
            it = QListWidgetItem(self.label_for(fp))
            it.setData(FP_ROLE, fp)
            if not self.lib.is_online(fp):
                it.setForeground(QColor(150, 150, 150))
            return it

        def refresh_lists(self):
            self.order_label.setText(
                f"{'Chronological' if self.current_order == 'chrono' else 'Upload'} order "
                f"— {len(self.lib.order(self.current_order))} episodes"
            )
            self.main_list.blockSignals(True)
            self.main_list.clear()
            for fp in self.lib.order(self.current_order):
                self.main_list.addItem(self._make_item(fp))
            self.main_list.blockSignals(False)

            self.unsorted_list.clear()
            for fp in self.lib.unsorted(self.current_order):
                self.unsorted_list.addItem(self._make_item(fp))

            online = len(self.lib.path_by_fp)
            self.status.showMessage(
                f"{online} videos on disk · {len(self.lib.videos)} known · "
                f"{self.unsorted_list.count()} unsorted in this order"
            )

        # ---- actions ------------------------------------------------------ #
        def _apply_mode(self):
            """Configure controls/labels for the current order's interaction model."""
            upload = self.current_order == "upload"
            self.add_btn.setVisible(not upload)
            self.remove_btn.setVisible(not upload)
            if upload:
                self.unsorted_hdr.setText("No upload date yet")
                self.unsorted_hdr.setToolTip(
                    "Videos with no upload date to sort by. Select one and tick "
                    "'Override date' in Details to place it in the timeline."
                )
                self.legend.setText(
                    "Upload order — sorted automatically by each episode's upload date "
                    "(shown per row; * = a date you overrode).  ✓ = watched · [S3+] = side-story · "
                    "[offline] = file missing.  Dragging only reorders episodes that share the same date."
                )
            else:
                self.unsorted_hdr.setText("Not in this order yet")
                self.unsorted_hdr.setToolTip(
                    "Videos on disk not placed in the chronological order yet."
                )
                self.legend.setText(
                    "Chronological order — your story timeline.  ✓ = watched · [S3] = Season 3 · "
                    "[S3+] = side-story · [offline] = file missing.  Drag rows to reorder.  Double-click to play."
                )

        def on_order_changed(self):
            self.current_order = self.order_combo.currentData()
            self.lib.last_order = self.current_order
            self.lib.save()
            self._apply_mode()
            self.refresh_lists()

        def on_reordered(self):
            fps = [self.main_list.item(i).data(FP_ROLE) for i in range(self.main_list.count())]
            self.lib.set_order(self.current_order, fps)
            self.lib.save()
            if self.current_order == "upload":
                # re-render so cross-date drags snap back (date stays primary)
                self.refresh_lists()
                self.status.showMessage("Same-day order saved.", 2000)
            else:
                self.status.showMessage("Order saved.", 2000)

        def _startup_metadata(self):
            """One-time: read upload dates/URLs from files, then build upload order."""
            missing = self.lib.videos_missing_meta() if HAVE_FFPROBE else []
            if missing:
                self._run_meta_fetch(missing, "Reading upload dates from video files…")
            self.lib.migrate_if_needed()
            self.lib.save()
            self._apply_mode()
            self.refresh_lists()

        def _run_meta_fetch(self, fps, label):
            # Guard against re-entry: the loop calls processEvents(), so without
            # this a second trigger (startup timer, Refresh button) could nest.
            if getattr(self, "_busy", False):
                return
            self._busy = True
            try:
                dlg = QProgressDialog(label, "Cancel", 0, len(fps), self)
                dlg.setWindowTitle("Chronicle")
                dlg.setWindowModality(Qt.ApplicationModal)
                dlg.setMinimumDuration(0)
                for i, fp in enumerate(fps):
                    if dlg.wasCanceled():
                        break
                    path = self.lib.path_by_fp.get(fp)
                    if path is not None:
                        self.lib.apply_meta(fp, read_embedded_meta(path))
                    dlg.setValue(i + 1)
                    QApplication.processEvents()
                dlg.close()
            finally:
                self._busy = False

        def on_refresh_dates(self):
            if not HAVE_FFPROBE:
                return
            self.lib.clear_meta_probed()
            self._run_meta_fetch(list(self.lib.path_by_fp), "Re-reading upload dates…")
            self.lib.save()
            self.refresh_lists()
            self.status.showMessage("Upload dates refreshed from files.", 4000)

        def on_select(self, item):
            if item is None:
                return
            fp = item.data(FP_ROLE)
            if fp:
                self.load_details(fp)

        def load_details(self, fp: str):
            self._selected_fp = fp
            v = self.lib.videos.get(fp, {})
            self._loading_details = True
            self.title_edit.setText(v.get("title", ""))
            season = v.get("season")
            has_season = isinstance(season, int)
            self.season_chk.setChecked(has_season)
            self.season_edit.setEnabled(has_season)
            self.season_edit.setValue(season if has_season else 0)
            self.side_edit.setChecked(bool(v.get("side_story")))
            self.watched_edit.setChecked(bool(v.get("watched")))
            self.note_edit.setPlainText(v.get("note", ""))
            # upload date: file value (label) + optional override
            file_date = v.get("upload_date")
            self.filedate_lbl.setText(file_date or "— (none in file)")
            override = v.get("upload_date_override")
            self.override_chk.setChecked(bool(override))
            self.date_edit.setEnabled(bool(override))
            shown = override or file_date
            self.date_edit.setDate(
                QDate.fromString(shown, "yyyy-MM-dd") if shown else QDate(2016, 1, 1)
            )
            self.yt_btn.setEnabled(bool(v.get("url")))
            self._loading_details = False

        def save_details(self):
            if self._loading_details or not self._selected_fp:
                return
            v = self.lib.videos.setdefault(self._selected_fp, default_video(""))
            v["title"] = self.title_edit.text().strip() or v["title"]
            v["season"] = self.season_edit.value() if self.season_chk.isChecked() else None
            v["side_story"] = self.side_edit.isChecked()
            v["watched"] = self.watched_edit.isChecked()
            v["note"] = self.note_edit.toPlainText()
            self.lib.save()
            self._refresh_row(self._selected_fp)

        def on_season_toggled(self, *args):
            # Enable/disable the number picker to mirror the checkbox, then save.
            self.season_edit.setEnabled(self.season_chk.isChecked())
            self.save_details()

        def on_date_changed(self, *args):
            # Enable/disable the picker to mirror the checkbox regardless of state.
            self.date_edit.setEnabled(self.override_chk.isChecked())
            if self._loading_details or not self._selected_fp:
                return
            v = self.lib.videos.get(self._selected_fp)
            if v is None:
                return
            if self.override_chk.isChecked():
                v["upload_date_override"] = self.date_edit.date().toString("yyyy-MM-dd")
            else:
                v["upload_date_override"] = None
            self.lib.save()
            fp = self._selected_fp
            self.refresh_lists()   # date change can move the row / (un)hide it
            self._reselect(fp)

        def open_youtube(self):
            if not self._selected_fp:
                return
            url = self.lib.videos.get(self._selected_fp, {}).get("url")
            if url:
                QDesktopServices.openUrl(QUrl(url))

        def _reselect(self, fp: str):
            for lst in (self.main_list, self.unsorted_list):
                for i in range(lst.count()):
                    if lst.item(i).data(FP_ROLE) == fp:
                        lst.setCurrentRow(i)
                        lst.scrollToItem(lst.item(i))
                        return

        def _refresh_row(self, fp: str):
            for lst in (self.main_list, self.unsorted_list):
                for i in range(lst.count()):
                    it = lst.item(i)
                    if it.data(FP_ROLE) == fp:
                        it.setText(self.label_for(fp))

        def toggle_watched(self):
            fp = self._current_fp(self.main_list)
            if not fp:
                return
            v = self.lib.videos.setdefault(fp, default_video(""))
            v["watched"] = not v.get("watched")
            self.lib.save()
            self._refresh_row(fp)
            if fp == self._selected_fp:
                self.watched_edit.setChecked(v["watched"])

        def add_to_order(self):
            fp = self._current_fp(self.unsorted_list)
            if not fp:
                return
            order = self.lib.order(self.current_order)
            if fp not in order:
                order.append(fp)
                self.lib.set_order(self.current_order, order)
                self.lib.save()
            self.refresh_lists()

        def remove_from_order(self):
            fp = self._current_fp(self.main_list)
            if not fp:
                return
            order = [x for x in self.lib.order(self.current_order) if x != fp]
            self.lib.set_order(self.current_order, order)
            self.lib.save()
            self.refresh_lists()

        def on_help(self):
            # A parentless, independent top-level window. Being unparented means
            # a window manager can never route its close button (X) to the main
            # window — the cause of the "closing Help closes the app" bug. It is
            # modeless and tracked so it isn't garbage-collected.
            existing = getattr(self, "_help_win", None)
            if existing is not None:
                existing.raise_()
                existing.activateWindow()
                return
            dlg = QDialog(None)  # no parent -> its own OS window
            dlg.setWindowTitle("How this app works")
            dlg.resize(600, 640)
            lay = QVBoxLayout(dlg)
            browser = QTextBrowser()
            browser.setOpenExternalLinks(False)
            browser.setHtml(
                "<h3>Chronicle</h3>"
                "<p>Two orderings of the same episodes, each independently editable.</p>"
                "<p><b>View</b> (top-left) switches between:</p>"
                "<ul>"
                "<li><b>Chronological</b> — the in-story timeline. A "
                "<b>manual</b> list: drag rows to build the order, including dragging "
                "side-stories into the middle of a season.</li>"
                "<li><b>Upload order</b> — sorted <b>automatically</b> by each episode's "
                "real upload date, read from the video files. No dragging needed. The "
                "date is shown on each row.</li>"
                "</ul>"
                "<h4>Upload order &amp; dates</h4>"
                "<p>On first launch the app reads the upload date and YouTube link out of "
                "every file's embedded metadata (one-time; <b>Refresh dates from files</b> "
                "re-reads them). The Upload view then sorts itself by date.</p>"
                "<p>When two episodes share the same date, the app can't know the order, so "
                "in the Upload view you may <b>drag episodes that share a date</b> to fine-tune "
                "them — the nudge is saved. Dragging across different dates snaps back (date "
                "always wins).</p>"
                "<p>In <b>Details</b>, <b>File upload date</b> shows what was read from the "
                "file. Tick <b>Override date</b> and pick a date to move an episode to a "
                "different day (e.g. if the file's date is wrong); a <code>*</code> marks an "
                "overridden date on the row. Untick to go back to the file's date.</p>"
                "<p><b>Open on YouTube</b> opens the episode's source video in your browser.</p>"
                "<h4>Chronological order</h4>"
                "<p><b>Left list</b> = episodes in the chosen order; drag to reorder, "
                "double-click to play. <b>Right list</b> = episodes not in this order yet "
                "(in Upload view: episodes with no date); use <b>← Add to order</b> "
                "(chrono only) then drag into place. <b>Remove from list →</b> takes an "
                "episode out of <i>this</i> order only — nothing is ever deleted.</p>"
                "<p><b>Details</b> also edits title, season, watched, notes, and a "
                "<b>Side story</b> flag (adds a '+' to the badge; doesn't move anything).</p>"
                "<p>Row format: <code>✓ [S3+] Title</code> → watched, Season 3, side-story. "
                "<code>[offline]</code> means the file isn't found right now (its order and "
                "notes are kept).</p>"
                "<p><b>Continue watching</b> jumps to the last episode you played in this "
                "order. <b>Re-import from folders</b> rebuilds the chronological order from "
                "the original folder names (rarely needed; keeps your notes).</p>"
                f"<p>Everything is saved to <code>{STORE_NAME}</code> next to the videos. "
                "The app never modifies your video files.</p>"
            )
            lay.addWidget(browser)
            close_btn = QPushButton("Close")
            close_btn.clicked.connect(dlg.close)
            lay.addWidget(close_btn)
            dlg.setAttribute(Qt.WA_DeleteOnClose, True)
            dlg.destroyed.connect(lambda *_: setattr(self, "_help_win", None))
            self._help_win = dlg
            dlg.show()

        def on_continue(self):
            fp = self.lib.position.get(self.current_order) or self.lib.first_unwatched(self.current_order)
            if not fp:
                self.status.showMessage("Nothing to continue.", 3000)
                return
            for i in range(self.main_list.count()):
                if self.main_list.item(i).data(FP_ROLE) == fp:
                    self.main_list.setCurrentRow(i)
                    self.main_list.scrollToItem(self.main_list.item(i))
                    break
            self.play(fp)

        def on_reimport(self):
            box = QMessageBox(None)  # parentless: its X can't close the app
            box.setWindowModality(Qt.ApplicationModal)
            box.setWindowTitle("Re-import from folders")
            box.setText(
                "Re-read the current 'season … (Upload #N)' folder names to seed metadata.\n\n"
                "• Reset: overwrite both orders with the folder-derived order.\n"
                "• Merge: keep your current orders, just append any new videos.\n\n"
                "Notes and watched-state are always preserved."
            )
            reset = box.addButton("Reset orders", QMessageBox.DestructiveRole)
            merge = box.addButton("Merge", QMessageBox.AcceptRole)
            box.addButton(QMessageBox.Cancel)
            box.exec()
            clicked = box.clickedButton()
            if clicked not in (reset, merge):
                return
            summary = self.lib.import_from_folders(reset_orders=(clicked is reset))
            self.lib.save()
            self.refresh_lists()
            self.status.showMessage(
                f"Imported {summary['videos']} videos from {summary['folders']} folders.", 5000
            )

        # ---- playback ----------------------------------------------------- #
        def _current_fp(self, lst) -> str | None:
            it = lst.currentItem()
            return it.data(FP_ROLE) if it else None

        def play_selected(self, lst):
            fp = self._current_fp(lst)
            if fp:
                self.play(fp)

        def play(self, fp: str):
            path = self.lib.path_by_fp.get(fp)
            if not path or not path.exists():
                QMessageBox.warning(None, "Unavailable",
                                    "This video is not currently on disk (offline).")
                return
            if not player_exe:
                QMessageBox.warning(None, "No player", "No media player available.")
                return
            if player_name == "mpv":
                watchdir = self.lib.root / ".mpvwatchlater"
                args = ["--save-position-on-quit",
                        f"--watch-later-dir={watchdir}", str(path)]
            else:
                args = [str(path)]
            ok = QProcess.startDetached(player_exe, args)
            if not ok:
                QMessageBox.warning(None, "Launch failed",
                                    f"Could not start {player_name}.")
                return
            self.lib.position[self.current_order] = fp
            self.lib.videos.setdefault(fp, default_video(path.name))["last_opened"] = now_iso()
            self.lib.save()
            self.status.showMessage(f"Playing: {self.lib.videos[fp]['title']}", 4000)

    app = QApplication.instance() or QApplication(sys.argv)
    # Don't auto-quit when a dialog (Help / Re-import / warning) is closed via
    # its window-manager X. Only the main window closing ends the app — see
    # Window.closeEvent.
    app.setQuitOnLastWindowClosed(False)
    win = Window()
    win.show()
    return app.exec()


# --------------------------------------------------------------------------- #
#  Section 8: self-test (pure logic, no Qt, no writes to the app folder)       #
# --------------------------------------------------------------------------- #

# Synthetic fixtures exercising every quirk of the folder-name grammar:
# empty titles, no dash before "(Upload", multi-dash titles, sub-seasons and
# side-story letters. Codes/upload numbers matter to the tests; titles do not.
SAMPLE_FOLDERS = [
    "season 00x01 - Origins - (Upload #03)",
    "season 00x01a - Origins - Side Tale - (Upload #24)",
    "season 00x01b - Bonus Tales (Upload #10)",
    "season 00x02 - Origins - (Upload #16)",
    "season 00x02a - Graduation - (Upload #19)",
    "season 00x03 - Campus - (Upload #20)",
    "season 00x03a - Side Arc - The Move (Upload #01)",
    "season 01 - (Upload #02)",
    "season 01a - Remake - (Upload #26)",
    "season 02 - (Upload #04)",
    "season 02a - Detours - (Upload #05)",
    "season 02b - The Move - (Upload #06)",
    "season 03 - (Upload #07)",
    "season 03a - Festival - (Upload #08)",
    "season 03b - Swap - (Upload #09)",
    "season 03c - Holiday - (Upload #11)",
    "season 03d - Return - (Upload #12)",
    "season 03e - New Year - (Upload #13)",
    "season 03f - Roleplay - (Upload #14)",
    "season 04 - (Upload #15)",
    "season 04a - The Year - (Upload #17)",
    "season 05 - (Upload #18)",
    "season 06 - (Upload #25)",
    "season 06a - Winter - Her Wish - (Upload #21)",
    "season 06b - side story - D&D - (Upload #22)",
    "season 06c - side story - (Upload #23)",
]


def selftest() -> int:
    import tempfile

    fails = []

    def check(name, cond):
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
        if not cond:
            fails.append(name)

    print("1. SEASON_RE parses all sample folder names")
    parsed = {f: parse_season_folder(f) for f in SAMPLE_FOLDERS}
    check("all 26 parse", all(v is not None for v in parsed.values()))
    check("empty title -> season 1, upload 2",
          parse_season_folder("season 01 - (Upload #02)") == ("01", 2, 1, False))
    check("no-dash-before-Upload (00x01b)",
          parse_season_folder("season 00x01b - Bonus Tales (Upload #10)")
          == ("00x01b", 10, 0, True))
    check("multi-dash title (00x03a)",
          parse_season_folder("season 00x03a - Side Arc - The Move (Upload #01)")
          == ("00x03a", 1, 0, True))

    print("2. code_sort_key -> canonical chronological order")
    codes = [parse_season_folder(f)[0] for f in SAMPLE_FOLDERS]
    shuffled = list(reversed(codes))
    expected = ["00x01", "00x01a", "00x01b", "00x02", "00x02a", "00x03", "00x03a",
                "01", "01a", "02", "02a", "02b", "03", "03a", "03b", "03c", "03d",
                "03e", "03f", "04", "04a", "05", "06", "06a", "06b", "06c"]
    check("sorts to canonical sequence", sorted(shuffled, key=code_sort_key) == expected)
    check("unparseable code -> end", code_sort_key("weird")[0] == 9999)

    print("3. uploads map 1..26 distinctly")
    uploads = sorted(parse_season_folder(f)[1] for f in SAMPLE_FOLDERS)
    check("uploads == 1..26", uploads == list(range(1, 27)))

    print("4. clean_title strips prefix + normalizes fullwidth")
    check("prefix stripped + ｜ -> |",
          clean_title("02 - Night Watch ｜ [Part 2] FINALE.mkv")
          == "Night Watch | [Part 2] FINALE")
    check("apostrophe normalized", "'" in clean_title("01 - A Hero’s Tale.mkv"))

    print("5. fingerprint + cache behavior")
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        a = tdp / "a.mkv"
        a.write_bytes(b"hello world" * 100)
        fp1 = fingerprint(a)
        b = tdp / "moved_renamed.mkv"
        a.rename(b)
        fp2 = fingerprint(b)
        check("fingerprint stable across move+rename", fp1 == fp2)
        b.write_bytes(b"different content entirely")
        check("fingerprint changes on content change", fingerprint(b) != fp1)

    print("6. Library scan + import + ordering + persistence round-trip")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # build a tiny fake collection: 2 folders, distinct content per file
        specs = [
            ("season 01 - (Upload #02)", ["01 - Ep One.mkv", "02 - Ep Two.mkv"]),
            ("season 00x01a - Side Tale - (Upload #24)", ["01 - Side.mkv"]),
        ]
        n = 0
        for folder, files in specs:
            (root / folder).mkdir()
            for fn in files:
                (root / folder / fn).write_bytes(f"content-{n}".encode())
                n += 1
        lib = Library(root)
        lib.load()
        lib.scan()
        check("scan finds 3 videos", len(lib.path_by_fp) == 3)
        lib.import_from_folders()
        check("chrono_order length 3", len(lib.chrono_order) == 3)
        # chrono: 00x01a sorts before 01
        first_fp = lib.chrono_order[0]
        check("chrono first is the 00x01a side story",
              lib.videos[first_fp]["orig_folder"].startswith("season 00x01a"))
        check("season derived (0 for 00x01a)", lib.videos[first_fp]["season"] == 0)
        check("side_story flag set", lib.videos[first_fp]["side_story"] is True)
        lib.save()

        # round-trip
        lib2 = Library(root)
        lib2.load()
        check("round-trip preserves chrono_order", lib2.chrono_order == lib.chrono_order)

        # reorder persists
        rev = list(reversed(lib2.chrono_order))
        lib2.set_order("chrono", rev)
        lib2.save()
        lib3 = Library(root)
        lib3.load()
        check("reorder persisted", lib3.chrono_order == rev)

        # offline vs unsorted after a file is removed and one added
        (root / specs[0][0] / specs[0][1][0]).unlink()  # remove one
        (root / specs[0][0] / "03 - New Ep.mkv").write_bytes(b"content-new")
        lib3.scan()
        check("removed fp is offline (kept in order)",
              any(not lib3.is_online(fp) for fp in lib3.chrono_order))
        check("new video shows as unsorted",
              len(lib3.unsorted("chrono")) == 1)

        # corrupt store -> backup + empty
        lib3.store_path.write_text("{ this is not valid json", encoding="utf-8")
        lib4 = Library(root)
        lib4.load()
        check("corrupt store -> empty start", lib4.videos == {})
        check("corrupt store -> .bak created",
              lib3.store_path.with_suffix(".json.bak").exists())

    print("7. date parsing (_date8_to_iso)")
    check("YYYYMMDD -> ISO", _date8_to_iso("20151123") == "2015-11-23")
    check("date embedded in noise", _date8_to_iso("date=20180505x") == "2018-05-05")
    check("empty -> None", _date8_to_iso("") is None)
    check("garbage -> None", _date8_to_iso("notadate") is None)
    check("impossible month -> None", _date8_to_iso("20151399") is None)

    print("8. upload order derived from dates (+ ties, undated, override)")
    with tempfile.TemporaryDirectory() as td:
        lib = Library(Path(td))
        def mk(fp, date, ci, override=None):
            v = default_video(fp + ".mkv")
            v["upload_date"] = date
            v["upload_date_override"] = override
            v["meta_probed"] = True
            lib.videos[fp] = v
        # a<b, then three sharing 2018-05-05, plus one undated
        mk("a", "2015-11-03", 0); mk("b", "2015-11-23", 1)
        mk("c", "2018-05-05", 2); mk("d", "2018-05-05", 3); mk("e", "2018-05-05", 4)
        mk("u", None, 5)
        lib.path_by_fp = {k: Path(td) / (k + ".mkv") for k in "abcdeu"}
        lib.chrono_order = list("abcdeu")
        lib.schema_loaded = 1
        lib.migrate_if_needed()
        check("upload sorted by date, chrono tiebreak", lib.order("upload") == list("abcde"))
        check("undated excluded from upload order", "u" not in lib.order("upload"))
        check("undated appears in unsorted(upload)", lib.unsorted("upload") == ["u"])
        # within-day nudge: move e ahead of c (both 2018-05-05)
        vis = lib.order("upload"); vis.remove("e"); vis.insert(vis.index("c"), "e")
        lib.set_order("upload", vis)
        check("within-day nudge persists", lib.order("upload") == list("abecd"))
        check("nudge did NOT move across dates", lib.order("upload")[:2] == ["a", "b"])
        # override d onto an earlier day -> relocates; ties elsewhere unchanged
        lib.videos["d"]["upload_date_override"] = "2015-11-10"
        check("override relocates by effective date", lib.order("upload") == list("adbec"))
        check("effective_date uses override", effective_date(lib.videos["d"]) == "2015-11-10")

    print("9. migration from a v1 store (folder-based, no dates)")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # hand-write a v1 store: has chrono + a stale folder-based upload_order,
        # videos without dates.
        v1 = {
            "schema_version": 1,
            "chrono_order": ["x", "y"],
            "upload_order": ["y", "x"],  # stale, must be superseded
            "videos": {
                "x": {**default_video("x.mkv"), "note": "keep me", "watched": True},
                "y": default_video("y.mkv"),
            },
        }
        (root / STORE_NAME).write_text(json.dumps(v1), encoding="utf-8")
        lib = Library(root)
        lib.load()
        check("loaded schema is 1", lib.schema_loaded == 1)
        # dates arrive (as ffprobe would provide): x newer than y
        lib.apply_meta("x", {"upload_date": "2016-02-02", "url": "http://u/x"})
        lib.apply_meta("y", {"upload_date": "2016-01-01", "url": None})
        lib.path_by_fp = {"x": root / "x.mkv", "y": root / "y.mkv"}
        migrated = lib.migrate_if_needed()
        check("migration ran", migrated is True and lib.schema_loaded == SCHEMA_VERSION)
        check("upload now date-sorted (y before x)", lib.order("upload") == ["y", "x"])
        check("chrono_order preserved", lib.chrono_order == ["x", "y"])
        check("notes/watched preserved", lib.videos["x"]["note"] == "keep me"
              and lib.videos["x"]["watched"] is True)

    print("10. read_embedded_meta on a real file (if ffprobe present)")
    if HAVE_FFPROBE:
        sample = next(Path(".").rglob("*.mkv"), None)
        if sample is not None:
            meta = read_embedded_meta(sample)
            check("real file yields an ISO date",
                  meta["upload_date"] is not None and len(meta["upload_date"]) == 10)
        else:
            print("  [SKIP] no .mkv in cwd")
    else:
        print("  [SKIP] ffprobe not available")

    print("11. resolve_player returns a tuple")
    name, _exe = resolve_player()
    check("player name is a str", isinstance(name, str))

    print()
    if fails:
        print(f"SELFTEST FAILED: {len(fails)} check(s) failed: {fails}")
        return 1
    print("SELFTEST OK — all checks passed.")
    return 0


# --------------------------------------------------------------------------- #
#  main                                                                        #
# --------------------------------------------------------------------------- #


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--selftest":
        return selftest()

    root = Path(args[0]).resolve() if args else Path(__file__).resolve().parent
    if not root.is_dir():
        print(f"Root is not a directory: {root}", file=sys.stderr)
        return 2

    lib = Library(root)
    lib.load()
    lib.scan()
    fresh = not lib.videos or (not lib.chrono_order and not lib.upload_order)
    if fresh:
        summary = lib.import_from_folders(reset_orders=True)
        print(f"First run: imported {summary['videos']} videos "
              f"from {summary['folders']} folders.")
    lib.save()

    try:
        return run_gui(lib)
    except ImportError:
        print("PySide6 is required for the GUI. Install it with:\n"
              "    pacman -S pyside6      # Arch/CachyOS\n"
              "    pip install pyside6    # any platform", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
