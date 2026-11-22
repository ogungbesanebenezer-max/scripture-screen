"""Book names offered while the operator types a verse: "re" -> Revelation, "jo" -> Joshua, Job, Joel, John..."""
import re

from .bible_names import BOOKS

_KEYS = [(book, book.lower().replace(" ", "")) for book in BOOKS]
_ORDINALS = {"first": "1", "1st": "1", "second": "2", "2nd": "2", "third": "3", "3rd": "3", "i": "1", "ii": "2",
             "iii": "3"}


def book_part(text):
    """The book the operator is typing, or None once they have moved on to the chapter (a digit after letters)."""
    text = text.lower().replace(".", " ").strip()
    if not text or re.search(r"[a-z]\s*\d", text):
        return None
    words = text.split()
    if words and words[0] in _ORDINALS and len(words) > 1:
        words[0] = _ORDINALS[words[0]]
    joined = "".join(words)
    return joined if re.fullmatch(r"[123]?[a-z]*", joined) and joined else None


def suggest_books(text, limit=8):
    """Books that start with what was typed first, then books with a later word that does ("jo" -> 1 John),
    each in Bible order."""
    typed = book_part(text)
    if not typed:
        return []
    starts = [book for book, key in _KEYS if key.startswith(typed)]
    inner = [] if typed[0].isdigit() else [
        book for book, _key in _KEYS if book not in starts
        and any(word.lower().startswith(typed) for word in book.split()[1:])]
    return (starts + inner)[:limit]


_REFERENCE = re.compile(r"^\s*(?P<book>(?:[123]|first|second|third|1st|2nd|3rd|i{1,3})?\s*[a-z][a-z .]*?)"
                        r"\s*(?P<chapter>\d+)?(?P<sep>\s*[:\s.]\s*)?(?P<verse>\d+)?\s*$", re.I)


def resolve_book(typed):
    """The full name for what was typed ("rev", "1 jo", "Revelation"), or None."""
    key = book_part(typed)
    if not key:
        return None
    exact = next((book for book, book_key in _KEYS if book_key == key), None)
    if exact:
        return exact
    found = suggest_books(typed, limit=1)
    return found[0] if found else None


def suggest(text, shape, verse_text=None, limit=40):
    """What to offer under the verse box, as (shown, filled_in, kind) with kind "book", "chapter" or "verse".

    shape: {book: {chapter: verses}} from the Bible. verse_text(book, chapter, verse) gives the words, so the
    operator sees which verse it is before choosing it."""
    books = suggest_books(text)
    done = len(books) == 1 and books[0].lower() == (text or "").strip().lower() and (text or "").endswith(" ")
    if books and not done:  # "Revelation " (name complete, space typed) moves on to its chapters
        return [(book, book + " ", "book") for book in books]
    match = _REFERENCE.match(text or "")
    ended = bool(re.search(r"[\s:.]$", text or ""))  # a space or ":" typed after the book or chapter
    separated = bool(match and match["sep"]) or ended
    if not match or match["chapter"] is None and not separated:
        return []
    book = resolve_book(match["book"])
    chapters = shape.get(book) if book else None
    if not chapters:
        return []
    chapter_typed, verse_typed = match["chapter"] or "", match["verse"] or ""
    if len(chapters) == 1 and chapter_typed and not separated:  # "Jude 5" is verse 5
        chapter_typed, verse_typed, verse_stage = "1", chapter_typed, True
    else:
        verse_stage = bool(chapter_typed and (separated or verse_typed))
    if not verse_stage:
        found = [c for c in sorted(chapters) if str(c).startswith(chapter_typed)]
        return [(f"{book} {c}", f"{book} {c} ", "chapter") for c in found[:limit]]
    chapter = int(chapter_typed)
    if chapter not in chapters:
        return []
    out = []
    for verse in range(1, chapters[chapter] + 1):
        if not str(verse).startswith(verse_typed):
            continue
        words = (verse_text(book, chapter, verse) or "") if verse_text else ""
        words = words if len(words) <= 70 else words[:67].rsplit(" ", 1)[0] + "..."
        out.append((f"{book} {chapter}:{verse}   {words}".rstrip(), f"{book} {chapter}:{verse}", "verse"))
        if len(out) >= limit:
            break
    return out
