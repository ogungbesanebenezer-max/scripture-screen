"""Owner 2026-11-17: typing "re" should offer Revelation, then its chapters, then its verses, so a wrong verse is
put right in moments."""
import gc
import tkinter as tk
from types import SimpleNamespace

import pytest

from verse_screen.bible import Library, Ref
from verse_screen.book_suggest import resolve_book, suggest, suggest_books


@pytest.fixture(scope="module")
def shape():
    return Library().shape


def test_books_from_the_first_letters():
    assert suggest_books("re") == ["Revelation"]
    assert suggest_books("jo")[:5] == ["Joshua", "Job", "Joel", "Jonah", "John"]
    assert "1 John" in suggest_books("jo")  # a later word matches too
    assert suggest_books("1 jo") == ["1 John"]
    assert suggest_books("first cor") == ["1 Corinthians"]
    assert suggest_books("Rev. ") == ["Revelation"]
    assert suggest_books("rev 3") == [] and suggest_books("") == []


def test_resolve_book():
    assert resolve_book("rev") == "Revelation"
    assert resolve_book("john") == "John" and resolve_book("1 john") == "1 John"
    assert resolve_book("xyz") is None


def test_chapters_then_verses(shape):
    words = lambda b, c, v: f"words of {b} {c}:{v}"
    assert [s for s, _f, _k in suggest("Revelation ", shape)][:2] == ["Revelation 1", "Revelation 2"]
    assert len(suggest("Revelation ", shape)) == 22
    assert [f for _s, f, _k in suggest("rev 2", shape)] == ["Revelation 2 ", "Revelation 20 ", "Revelation 21 ",
                                                           "Revelation 22 "]
    verses = suggest("rev 3 1", shape, words)
    assert verses[0] == ("Revelation 3:1   words of Revelation 3:1", "Revelation 3:1", "verse")
    assert [f for _s, f, _k in verses][1:3] == ["Revelation 3:10", "Revelation 3:11"]
    assert [f for _s, f, _k in suggest("rev 3:18", shape)] == ["Revelation 3:18"]
    assert [f for _s, f, _k in suggest("Jude 5", shape)] == ["Jude 1:5"]  # one chapter: the number is the verse
    assert suggest("rev 99 ", shape) == [] and suggest("1 cor 13 4-7", shape) == []


def test_long_verse_words_are_cut_at_a_word(shape):
    long = lambda b, c, v: "word " * 40
    shown = suggest("ps 119:1", shape, long)[0][0]
    assert shown.endswith("...") and len(shown) < 100


def _has_screen():
    try:
        tk.Tk().destroy()
        gc.collect()
        return True
    except tk.TclError:
        return False


from tests.test_media import make_app  # noqa: E402,F401


def key(app, text):
    app.search_entry.delete(0, "end")
    app.search_entry.insert(0, text)
    app._typing_verse(SimpleNamespace(keysym="x"))
    app.root.update()


ENTER = SimpleNamespace(keysym="Return")


@pytest.mark.skipif(not _has_screen(), reason="no screen available")
def test_type_re_then_pick_chapter_then_verse(make_app):
    app = make_app()  # AUTO on: a chosen verse goes straight up
    key(app, "re")
    assert app._books_shown() and app.book_list.get(0) == "Revelation"
    app._typed_search(ENTER)  # Enter takes it
    assert app.search_entry.get() == "Revelation " and app.book_list.get(2) == "Revelation 3"
    app._move_book(1)
    app._move_book(1)
    app._typed_search(ENTER)
    assert app.search_entry.get() == "Revelation 3 " and app.book_list.get(17).startswith("Revelation 3:18   I counsel")
    key(app, "Revelation 3 18")
    app._typed_search(ENTER)
    assert app.live_ref == Ref("Revelation", 3, 18) and not app._books_shown()


@pytest.mark.skipif(not _has_screen(), reason="no screen available")
def test_wrong_verse_opens_that_chapters_verses(make_app):
    app = make_app()
    key(app, "John 3 16")
    app._typed_search(None)
    app.wrong_verse()
    app.root.update()
    assert app._books_shown() and app.book_list.get(0).startswith("John 3:1 ")
    app.book_list.selection_clear(0, "end")
    app.book_list.selection_set(17)
    app._accept_book()  # a click on the right verse
    assert app.live_ref == Ref("John", 3, 18)


@pytest.mark.skipif(not _has_screen(), reason="no screen available")
def test_typing_a_full_reference_previews_it(make_app):
    app = make_app('{"auto": false}')
    key(app, "rom 8 28")
    assert app.preview_ref == Ref("Romans", 8, 28) and app.live_ref is None
    assert "ENTER" in app.alert.cget("text")
