"""Media library: announcement posters and pictures kept on this laptop, so nothing has to be added twice.

Each file is copied into media/files when it is added, so the USB stick or the download it came from can go.
"""
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps

from .bible import ROOT

MEDIA_DIR = ROOT / "media"
PICTURE_TYPES = (".jpg", ".jpeg", ".png", ".webp", ".gif")
VIDEO_TYPES = (".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".wmv", ".mpg", ".mpeg")
AUDIO_TYPES = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".wma", ".opus")
KINDS = {"announcement": "Announcement", "image": "Image", "video": "Video", "audio": "Music"}
ACCEPTS = {"announcement": PICTURE_TYPES + VIDEO_TYPES, "image": PICTURE_TYPES, "video": VIDEO_TYPES,
           "audio": AUDIO_TYPES}

SCHEMA = """
CREATE TABLE IF NOT EXISTS media_items (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    file_name TEXT NOT NULL,
    original_name TEXT NOT NULL DEFAULT '',
    added TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS announcements (
    media_id INTEGER PRIMARY KEY REFERENCES media_items(id) ON DELETE CASCADE,
    event_date TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    show_from TEXT NOT NULL DEFAULT '',
    show_until TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS hymns (
    id INTEGER PRIMARY KEY,
    number TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    collection TEXT NOT NULL DEFAULT '',
    ordered INTEGER NOT NULL DEFAULT 0,
    added TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hymn_stanzas (
    hymn_id INTEGER NOT NULL REFERENCES hymns(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    label TEXT NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY (hymn_id, position)
);
CREATE TABLE IF NOT EXISTS playlists (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS playlist_items (
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    item_type TEXT NOT NULL,
    item_id INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (playlist_id, position)
);
"""
CURRENT_ORDER = "Current service order"  # saved on every change, so a restart mid-service loses nothing


def today():
    return datetime.now().strftime("%Y-%m-%d")


def valid_day(text):
    """'' or a real YYYY-MM-DD date."""
    text = (text or "").strip()
    if not text:
        return ""
    try:
        return datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        raise ValueError(f"\"{text}\" is not a date. Write it like {today()}.") from None


@dataclass
class Item:
    id: int
    kind: str
    title: str
    path: Path
    added: str
    event_date: str = ""
    description: str = ""
    url: str = ""  # a video link, played as a stream: nothing saved on the laptop
    show_from: str = ""  # announcements: the days it is current (YYYY-MM-DD, either may be empty)
    show_until: str = ""

    def current(self, day=None):
        day = day or today()
        return (not self.show_from or self.show_from <= day) and (not self.show_until or day <= self.show_until)

    @property
    def schedule_note(self):
        if self.kind != "announcement" or self.current():
            return ""
        return f"from {self.show_from}" if self.show_from and today() < self.show_from else "ended"

    @property
    def is_audio(self):
        return self.path.suffix.lower() in AUDIO_TYPES

    @property
    def is_animated(self):
        """An animated GIF: played by the video player so it moves (and loops)."""
        if self.path.suffix.lower() != ".gif" or not self.path.exists():
            return False
        try:
            with Image.open(self.path) as picture:
                return getattr(picture, "n_frames", 1) > 1
        except Exception:
            return False

    @property
    def is_video(self):
        return bool(self.url) or self.path.suffix.lower() in VIDEO_TYPES

    @property
    def label(self):
        return f"{self.title} · {self.event_date}" if self.event_date else self.title


def load_picture(path):
    """The picture as the TV should show it: phone photos turned upright, see-through parts on black."""
    with Image.open(path) as opened:
        picture = ImageOps.exif_transpose(opened)
        picture.load()
    if picture.mode in ("RGBA", "LA", "P") and ("transparency" in picture.info or picture.mode != "P"):
        picture = picture.convert("RGBA")
        black = Image.new("RGBA", picture.size, (0, 0, 0, 255))
        return Image.alpha_composite(black, picture).convert("RGB")
    return picture.convert("RGB")


class MediaLibrary:
    def __init__(self, folder=None):
        self.folder = Path(folder or MEDIA_DIR)
        self.files = self.folder / "files"
        self.files.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.folder / "media.db")
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)
        for table, column in (("media_items", "url"), ("announcements", "show_from"),
                              ("announcements", "show_until")):  # a library made by an earlier version
            if column not in [row[1] for row in self.db.execute(f"PRAGMA table_info({table})")]:
                with self.db:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")

    def add(self, source, kind, title=None):
        return self.register(kind, *self.store(source, kind), title=title)

    def store(self, source, kind):
        """Check and copy one file into the library. Touches no database, so a big video can copy on a worker
        thread while the screen carries on."""
        source = Path(source)
        if kind not in KINDS:
            raise ValueError(f"Unknown kind: {kind}")
        suffix = source.suffix.lower()
        if suffix not in ACCEPTS[kind]:
            allowed = {"image": "pictures (JPG, PNG, WebP, GIF)", "video": "videos (MP4, MOV, MKV, WebM, AVI, WMV)",
                       "audio": "music or recordings (MP3, WAV, M4A, AAC, OGG, FLAC, WMA)"}.get(kind, "pictures or videos")
            raise ValueError(f"{source.name}: only {allowed} can be added here.")
        if suffix in PICTURE_TYPES:
            try:
                with Image.open(source) as picture:
                    picture.verify()
            except Exception:
                raise ValueError(f"{source.name} could not be read as a picture.") from None
        elif source.stat().st_size == 0:
            raise ValueError(f"{source.name} is empty.")
        name = f"{uuid.uuid4().hex}{suffix}"
        try:
            shutil.copy2(source, self.files / name)
        except OSError as error:
            (self.files / name).unlink(missing_ok=True)
            raise ValueError(f"{source.name} could not be copied: {error.strerror or error}") from None
        return name, source.name

    def register(self, kind, file_name, original_name, title=None, url=""):
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO media_items (kind, title, file_name, original_name, added, url) VALUES (?, ?, ?, ?, ?, ?)",
                (kind, (title or Path(original_name).stem).strip() or "Untitled", file_name, original_name,
                 datetime.now().strftime("%Y-%m-%d %H:%M"), url))
            if kind == "announcement":
                self.db.execute("INSERT INTO announcements (media_id) VALUES (?)", (cursor.lastrowid,))
        return self.get(cursor.lastrowid)

    def add_link(self, url, title, kind="video"):
        return self.register(kind, "", url, title=title, url=url.strip())

    _SELECT = ("SELECT m.id, m.kind, m.title, m.file_name, m.added, COALESCE(a.event_date, ''), "
               "COALESCE(a.description, ''), m.url, COALESCE(a.show_from, ''), COALESCE(a.show_until, '') FROM media_items m LEFT JOIN announcements a ON a.media_id = m.id")

    def _item(self, row):
        return Item(row[0], row[1], row[2], self.files / row[3] if row[3] else Path(), *row[4:])

    def get(self, item_id):
        row = self.db.execute(self._SELECT + " WHERE m.id = ?", (item_id,)).fetchone()
        return self._item(row) if row else None

    def items(self, kind, search=""):
        pattern = f"%{search.strip()}%"
        rows = self.db.execute(self._SELECT + " WHERE m.kind = ? AND (m.title LIKE ? OR COALESCE(a.description, '') "
                               "LIKE ?) ORDER BY m.id DESC", (kind, pattern, pattern))
        return [self._item(row) for row in rows]

    def rename(self, item_id, title):
        title = title.strip()
        if title:
            with self.db:
                self.db.execute("UPDATE media_items SET title = ? WHERE id = ?", (title, item_id))

    def describe(self, item_id, event_date, description):
        with self.db:
            self.db.execute("UPDATE announcements SET event_date = ?, description = ? WHERE media_id = ?",
                            (event_date.strip(), description.strip(), item_id))

    def schedule(self, item_id, show_from, show_until):
        start, end = valid_day(show_from), valid_day(show_until)
        if start and end and end < start:
            raise ValueError("The last day is before the first day.")
        with self.db:
            self.db.execute("UPDATE announcements SET show_from = ?, show_until = ? WHERE media_id = ?",
                            (start, end, item_id))

    def current_announcements(self, day=None):
        """Pictures that are current today, oldest first: the order a pre-service loop shows them."""
        return [item for item in reversed(self.items("announcement"))
                if item.current(day) and not item.url and item.path.suffix.lower() in PICTURE_TYPES
                and item.path.exists()]

    # ---- hymns ----
    def add_hymn(self, title, stanzas, number="", collection="", ordered=False):
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO hymns (number, title, collection, ordered, added) VALUES (?, ?, ?, ?, ?)",
                (str(number or "").strip(), title.strip() or "Untitled hymn", (collection or "").strip(),
                 int(bool(ordered)), datetime.now().strftime("%Y-%m-%d %H:%M")))
            self._put_stanzas(cursor.lastrowid, stanzas)
        return cursor.lastrowid

    def _put_stanzas(self, hymn_id, stanzas):
        self.db.execute("DELETE FROM hymn_stanzas WHERE hymn_id = ?", (hymn_id,))
        self.db.executemany("INSERT INTO hymn_stanzas (hymn_id, position, label, text) VALUES (?, ?, ?, ?)",
                            [(hymn_id, i, s.label, s.text) for i, s in enumerate(stanzas)])

    def update_hymn(self, hymn_id, title, stanzas, number="", collection="", ordered=None):
        with self.db:
            self.db.execute("UPDATE hymns SET title = ?, number = ?, collection = ? WHERE id = ?",
                            (title.strip() or "Untitled hymn", str(number or "").strip(), (collection or "").strip(),
                             hymn_id))
            if ordered is not None:
                self.db.execute("UPDATE hymns SET ordered = ? WHERE id = ?", (int(bool(ordered)), hymn_id))
            self._put_stanzas(hymn_id, stanzas)

    def get_hymn(self, hymn_id):
        from .hymns import Hymn, Stanza

        row = self.db.execute("SELECT id, title, number, collection, ordered FROM hymns WHERE id = ?",
                              (hymn_id,)).fetchone()
        if not row:
            return None
        stanzas = [Stanza(label, text) for label, text in self.db.execute(
            "SELECT label, text FROM hymn_stanzas WHERE hymn_id = ? ORDER BY position", (hymn_id,))]
        return Hymn(row[0], row[1], row[2], row[3], bool(row[4]), stanzas)

    def hymns(self, search=""):
        """By number when a number is typed, else by title or any words in the lyrics."""
        words = search.strip()
        if words.isdigit():
            rows = self.db.execute("SELECT id FROM hymns WHERE number = ? OR number LIKE ? ORDER BY "
                                   "CAST(number AS INTEGER), title", (words, f"{words}%"))
        else:
            pattern = f"%{words}%"
            rows = self.db.execute(
                "SELECT DISTINCT h.id FROM hymns h LEFT JOIN hymn_stanzas s ON s.hymn_id = h.id WHERE h.title LIKE ? "
                "OR h.collection LIKE ? OR s.text LIKE ? ORDER BY (h.number = ''), CAST(h.number AS INTEGER), h.title",
                (pattern, pattern, pattern))
        return [self.get_hymn(row[0]) for row in rows.fetchall()]

    def delete_hymn(self, hymn_id):
        with self.db:
            self.db.execute("DELETE FROM hymns WHERE id = ?", (hymn_id,))

    # ---- service order and saved playlists ----
    def playlists(self):
        return [name for (name,) in self.db.execute(
            "SELECT name FROM playlists WHERE name != ? ORDER BY name", (CURRENT_ORDER,))]

    def save_playlist(self, name, entries):
        """entries: [(item_type, item_id)], item_type "media", "hymn" or "scripture"."""
        name = name.strip()
        if not name:
            raise ValueError("Give the service order a name.")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO playlists (name, created) VALUES (?, ?)",
                            (name, datetime.now().strftime("%Y-%m-%d %H:%M")))
            (playlist_id,) = self.db.execute("SELECT id FROM playlists WHERE name = ?", (name,)).fetchone()
            self.db.execute("DELETE FROM playlist_items WHERE playlist_id = ?", (playlist_id,))
            self.db.executemany(
                "INSERT INTO playlist_items (playlist_id, position, item_type, item_id) VALUES (?, ?, ?, ?)",
                [(playlist_id, i, kind, int(item_id or 0)) for i, (kind, item_id) in enumerate(entries)])

    def playlist(self, name):
        """The entries still in the library: something deleted since is quietly left out."""
        rows = self.db.execute(
            "SELECT i.item_type, i.item_id FROM playlist_items i JOIN playlists p ON p.id = i.playlist_id "
            "WHERE p.name = ? ORDER BY i.position", (name,)).fetchall()
        return [(kind, item_id) for kind, item_id in rows if kind == "scripture"
                or (kind == "media" and self.get(item_id)) or (kind == "hymn" and self.get_hymn(item_id))]

    def delete_playlist(self, name):
        with self.db:
            self.db.execute("DELETE FROM playlists WHERE name = ?", (name,))

    def delete(self, item_id):
        item = self.get(item_id)
        if not item:
            return
        with self.db:
            self.db.execute("DELETE FROM media_items WHERE id = ?", (item_id,))
        try:
            if item.path.name:
                item.path.unlink(missing_ok=True)
        except OSError:
            pass  # the row is gone; a file Windows still holds open is only wasted space

    def close(self):
        self.db.close()
