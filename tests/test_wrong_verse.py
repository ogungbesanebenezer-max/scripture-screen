"""Rev 3:18 came up as Rev 3:1 on the TV (2026-11-11): the number was cut, and there was no quick way to fix it."""
import time
from types import SimpleNamespace

import pytest

from verse_screen.app import App
from verse_screen.bible import Library, Ref
from verse_screen.references import ReferenceFinder

library = Library()
finder = ReferenceFinder(library)


@pytest.mark.parametrize("text", ["Revelation 3 verse 1 8", "Revelation 3 verse one eight", "Media give me Revelation 3 verse 1 8"])
def test_digits_said_one_by_one_make_one_verse(text):
    assert finder.find(text)[0].ref == Ref("Revelation", 3, 18)


def test_a_real_range_is_untouched():
    assert finder.find("Revelation 3 verse 1 to 8")[0].ref == Ref("Revelation", 3, 1, 8)


def live_app(ref, shown_ago):
    return SimpleNamespace(live_ref=ref, live_shown=time.monotonic() - shown_ago)


def test_cut_off_verse_is_completed_on_screen():
    app = live_app(Ref("Revelation", 3, 1), 3)
    assert App._completes_live(app, Ref("Revelation", 3, 18))
    assert not App._completes_live(app, Ref("Revelation", 3, 2))
    assert not App._completes_live(app, Ref("Revelation", 4, 18))
    assert not App._completes_live(live_app(Ref("Revelation", 3, 1), 60), Ref("Revelation", 3, 18))


class Entry:
    def __init__(self):
        self.text = "old"
    def delete(self, *_):
        self.text = ""
    def insert(self, _i, text):
        self.text += text
    def focus_set(self):
        pass
    def icursor(self, _):
        pass


def test_wrong_verse_takes_it_off_and_prefills_book_and_chapter():
    calls = []
    app = SimpleNamespace(live_ref=Ref("Revelation", 3, 1), preview_ref=None, undo_stack=[(Ref("John", 3, 16), "KJV")],
                          undo_live=lambda: calls.append("undo"), clear_live=lambda: calls.append("clear"),
                          search_entry=Entry(), alert=SimpleNamespace(config=lambda **k: None), _refresh_choices=lambda: None)
    App.wrong_verse(app)
    assert calls == ["undo"]
    assert app.search_entry.text == "Revelation 3 "
    app.live_ref, app.undo_stack = Ref("Revelation", 3, 1), []
    App.wrong_verse(app)
    assert calls == ["undo", "clear"]  # nothing before it: the church logo
