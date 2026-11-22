"""Read-only access to church_bible.db."""
import sqlite3
import threading
from dataclasses import dataclass

from .bible_names import BOOK_SET, BOOKS, SINGLE_CHAPTER  # noqa: F401
from .database import DB_PATH, ROOT  # noqa: F401


@dataclass(frozen=True)
class Ref:
    book: str
    chapter: int
    verse: int
    end_verse: int | None = None

    @property
    def label(self):
        name = "Psalm" if self.book == "Psalms" else self.book
        tail = f"-{self.end_verse}" if self.end_verse and self.end_verse != self.verse else ""
        return f"{name} {self.chapter}:{self.verse}{tail}"


class Library:
    def __init__(self, db_path=DB_PATH):
        if not db_path.exists():
            raise RuntimeError("church_bible.db not found. Run setup first.")
        self.conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True, check_same_thread=False)
        self.lock = threading.Lock()
        rows = self._query("SELECT code, name FROM translations ORDER BY code = 'KJV' DESC, code")
        self.names = dict(rows)
        self.shape = {}
        for book, chapter, top in self._query("SELECT book, chapter, MAX(verse) FROM scriptures "
                                              "GROUP BY book, chapter"):
            self.shape.setdefault(book, {})[chapter] = top

    def _query(self, sql, parameters=()):
        with self.lock:
            return self.conn.execute(sql, parameters).fetchall()

    @property
    def codes(self):
        return list(self.names)

    def exists(self, book, chapter, verse=1):
        return 1 <= verse <= self.shape.get(book, {}).get(chapter, 0)

    def text(self, code, ref):
        """Return the verse text, or None if this translation lacks it."""
        last = ref.end_verse or ref.verse
        rows = self._query("SELECT verse, text FROM scriptures WHERE translation = ? AND book = ? AND chapter = ? "
                           "AND verse BETWEEN ? AND ? ORDER BY verse", (code, ref.book, ref.chapter, ref.verse, last))
        if not rows:
            return None
        return " ".join(f"{v} {t}" for v, t in rows) if last != ref.verse else rows[0][1]

    def keyword_search(self, terms, limit):
        """Best verses for an FTS5 OR-query over all translations; one row per reference."""
        return self._query(
            "SELECT s.book, s.chapter, s.verse, MIN(hit.rank) AS best FROM "
            "(SELECT rowid AS id, bm25(bible_search) AS rank FROM bible_search WHERE bible_search MATCH ? "
            "ORDER BY rank LIMIT 400) AS hit JOIN scriptures s ON s.id = hit.id "
            "GROUP BY s.book, s.chapter, s.verse ORDER BY best LIMIT ?", (terms, limit))

    def vector_rows(self):
        return self._query("SELECT book, chapter, verse, embedding FROM verse_vectors ORDER BY id")

    def verse_list(self):
        return [Ref(*row) for row in self._query(
            "SELECT book, chapter, verse FROM scriptures WHERE translation = 'KJV' ORDER BY id")]

    def step(self, ref, delta):
        """Return the verse before or after ref, crossing chapters and books."""
        verse = ((ref.end_verse or ref.verse) if delta > 0 else ref.verse) + delta
        book, chapter = ref.book, ref.chapter
        if self.exists(book, chapter, verse):
            return Ref(book, chapter, verse)
        if delta > 0:
            if self.exists(book, chapter + 1, 1):
                return Ref(book, chapter + 1, 1)
            index = BOOKS.index(book) + 1
            return Ref(BOOKS[index], 1, 1) if index < len(BOOKS) else ref
        if chapter > 1:
            return Ref(book, chapter - 1, self.shape[book][chapter - 1])
        index = BOOKS.index(book) - 1
        if index < 0:
            return ref
        previous = BOOKS[index]
        last_chapter = max(self.shape[previous])
        return Ref(previous, last_chapter, self.shape[previous][last_chapter])
