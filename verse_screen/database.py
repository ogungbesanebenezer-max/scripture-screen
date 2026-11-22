"""Build church_bible.db, the single SQLite file the app reads.

Sources: data/bibles/*.json (public domain, in the repo), my_bibles/*.json (licensed
translations, kept out of git) and data/verse_vectors.npy (paraphrase-search vectors).
"""
import json
import sqlite3
from pathlib import Path

import numpy as np

from .bible_names import BOOK_SET, BOOKS

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "church_bible.db"
BUNDLED_DIR = ROOT / "data" / "bibles"
PRIVATE_DIR = ROOT / "my_bibles"
VECTORS_PATH = ROOT / "data" / "verse_vectors.npy"

SCHEMA = """
CREATE TABLE translations (code TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE scriptures (
    id INTEGER PRIMARY KEY,
    translation TEXT NOT NULL,
    book TEXT NOT NULL,
    chapter INTEGER NOT NULL,
    verse INTEGER NOT NULL,
    text TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_ref ON scriptures(translation, book, chapter, verse);
CREATE VIRTUAL TABLE bible_search USING fts5(text, content='scriptures', content_rowid='id', tokenize='porter unicode61');
CREATE TABLE verse_vectors (
    id INTEGER PRIMARY KEY,
    book TEXT NOT NULL,
    chapter INTEGER NOT NULL,
    verse INTEGER NOT NULL,
    embedding BLOB NOT NULL
);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def signature(directories=None, vectors_path=None):
    """Names and sizes of the Bible files the database is built from. A changed list means: build again."""
    directories = (BUNDLED_DIR, PRIVATE_DIR) if directories is None else directories
    vectors_path = Path(VECTORS_PATH if vectors_path is None else vectors_path)
    files = [path for directory in directories for path in sorted(Path(directory).glob("*.json"))]
    if vectors_path.exists():
        files.append(vectors_path)
    return json.dumps([[path.name, path.stat().st_size] for path in files])


def is_stale(db_path=None, directories=None, vectors_path=None):
    """True when a Bible file was added, removed or replaced since the database was built."""
    db_path = Path(DB_PATH if db_path is None else db_path)
    if not db_path.exists():
        return True
    try:
        conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT value FROM meta WHERE key = 'files'").fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return True  # built before this check existed, or damaged
    return row is None or row[0] != signature(directories, vectors_path)


def read_translation(path):
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    books = data.get("books", data)
    unknown = set(books) - BOOK_SET
    if unknown:
        raise ValueError(f"unrecognised book names, e.g. {sorted(unknown)[:3]}")
    return str(data.get("code", path.stem)).upper(), data.get("name", path.stem), books


def _rows(code, books):
    for book in BOOKS:
        for chapter in sorted(books.get(book, {}), key=int):
            for verse in sorted(books[book][chapter], key=int):
                yield code, book, int(chapter), int(verse), books[book][chapter][verse]


def build(db_path=DB_PATH, directories=(BUNDLED_DIR, PRIVATE_DIR), vectors_path=VECTORS_PATH):
    """Create the database from scratch, replacing the old one only on success. Returns skipped-file notes."""
    db_path, notes = Path(db_path), []
    temp = db_path.with_suffix(".building")
    temp.unlink(missing_ok=True)
    conn = sqlite3.connect(temp)
    try:
        conn.executescript(SCHEMA)
        for directory in directories:
            for path in sorted(Path(directory).glob("*.json")):
                try:
                    code, name, books = read_translation(path)
                except (ValueError, AttributeError, OSError) as error:
                    notes.append(f"Skipped {path.name}: {error}")
                    continue
                conn.execute("INSERT OR REPLACE INTO translations VALUES (?, ?)", (code, name))
                conn.execute("DELETE FROM scriptures WHERE translation = ?", (code,))
                conn.executemany("INSERT INTO scriptures (translation, book, chapter, verse, text) "
                                 "VALUES (?, ?, ?, ?, ?)", _rows(code, books))
        conn.execute("INSERT INTO bible_search(bible_search) VALUES ('rebuild')")
        if Path(vectors_path).exists():
            _add_vectors(conn, vectors_path)
        else:
            notes.append("No vector file: paraphrase search will use keywords only.")
        if not conn.execute("SELECT 1 FROM scriptures LIMIT 1").fetchone():
            raise RuntimeError("No Bible files could be read.")
        conn.execute("INSERT INTO meta VALUES ('files', ?)", (signature(directories, vectors_path),))
        conn.commit()
    finally:
        conn.close()
    temp.replace(db_path)
    return notes


def _add_vectors(conn, vectors_path):
    vectors = np.load(vectors_path)
    refs = conn.execute("SELECT book, chapter, verse FROM scriptures WHERE translation = 'KJV' ORDER BY id").fetchall()
    if len(refs) != len(vectors):
        raise RuntimeError("Vector file does not match the KJV verse list.")
    conn.executemany("INSERT INTO verse_vectors (book, chapter, verse, embedding) VALUES (?, ?, ?, ?)",
                     ((*ref, vector.tobytes()) for ref, vector in zip(refs, vectors)))
