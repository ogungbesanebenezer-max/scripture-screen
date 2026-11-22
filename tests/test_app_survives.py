"""The whole app, with its real windows, put through the accidents of a live service (2026-11-12).

No speech engine is loaded, so this runs in seconds. It needs a screen (a virtual one is fine: xvfb-run).
"""
import gc
import json
import logging
import tkinter as tk
from types import SimpleNamespace

import pytest

from verse_screen import app as app_module
from verse_screen import reliability
from verse_screen.app import App
from verse_screen.bible import Ref


def _has_screen():
    try:
        tk.Tk().destroy()
        gc.collect()
        return True
    except tk.TclError:
        return False


pytestmark = pytest.mark.skipif(not _has_screen(), reason="no screen available")


@pytest.fixture
def make_app(tmp_path, monkeypatch):
    made = []
    monkeypatch.setattr(app_module, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(App, "_load_models", lambda self: None)  # no speech engine in this test
    monkeypatch.setattr(app_module.PhoneMic, "start", lambda self: False)  # no network port

    def build(settings_text=None):
        if settings_text is not None:
            (tmp_path / "settings.json").write_text(settings_text, encoding="utf-8")
        app = App(SimpleNamespace(model=None, simulate=None))
        app.root.update()
        made.append(app)
        return app

    yield build
    for app in made:
        try:
            app.root.destroy()
        except tk.TclError:
            pass
    made.clear()
    del app
    gc.collect()  # old windows must be thrown away on this thread; Tk aborts if a worker thread does it


def type_verse(app, text):
    app.search_entry.delete(0, "end")
    app.search_entry.insert(0, text)
    app._typed_search(None)
    app.root.update()


def test_typed_verse_goes_live(make_app):
    app = make_app()
    type_verse(app, "John 3 16")
    assert app.live_ref == Ref("John", 3, 16)
    assert app.projector.body.startswith("For God so loved")


def test_closing_the_verse_window_does_not_kill_the_screen(make_app):
    """On a one-screen laptop the verse window has an X. Someone closed it on 2026-11-09 and every verse after that
    failed with "invalid command name .!toplevel.!canvas"."""
    app = make_app()
    type_verse(app, "John 3 16")
    window = app.projector.window
    window.tk.call(window.protocol("WM_DELETE_WINDOW"))  # what the X button and Alt+F4 do
    app.root.update()
    assert window.winfo_exists()
    type_verse(app, "Genesis 1 1")
    assert app.live_ref == Ref("Genesis", 1, 1)
    assert app.projector.body.startswith("In the beginning")


def test_verse_window_is_rebuilt_if_it_is_ever_destroyed(make_app):
    app = make_app()
    type_verse(app, "John 3 16")
    app.projector.window.destroy()
    app.root.update()
    type_verse(app, "Genesis 1 1")
    app.root.update()
    assert app.projector.window.winfo_exists()
    assert app.live_ref == Ref("Genesis", 1, 1)
    assert app.projector.body.startswith("In the beginning")
    app.clear_live()
    app._watch_displays()
    app.root.update()


@pytest.mark.parametrize("damaged", ["", "{not json", "[1, 2]", '"text"', "null", '{"auto": "yes", "display": 7, '
                                     '"translation": "XXX", "second_translation": 5, "min_confidence": "high", '
                                     '"clear_phrases": "amen", "auto_clear_seconds": "soon", "microphone": 3}'])
def test_damaged_settings_file_still_starts(make_app, damaged):
    app = make_app(damaged)
    type_verse(app, "Psalm 23 1")
    assert app.live_ref == Ref("Psalms", 23, 1)
    assert isinstance(json.loads(app_module.SETTINGS_PATH.read_text(encoding="utf-8")), dict)


def test_no_sound_input_is_logged_once_not_every_three_seconds(make_app, monkeypatch, caplog):
    monkeypatch.setattr(app_module, "list_microphones", lambda refresh=False: [])
    app = make_app()
    app.mics = []
    monkeypatch.setattr(app.root, "after", lambda *a: None)
    with caplog.at_level(logging.WARNING):
        for _ in range(10):
            app._watch_audio()
    assert sum("Sound input lost" in r.message for r in caplog.records) == 1


def test_sound_input_coming_back_is_picked_up(make_app, monkeypatch):
    found = []
    monkeypatch.setattr(app_module, "list_microphones", lambda refresh=False: list(found))
    opened = []

    class FakeMic:
        def __init__(self, ring, index, muted=None):
            opened.append(index)

        def alive(self):
            return True

        def stop(self):
            pass

    monkeypatch.setattr(app_module, "Microphone", FakeMic)
    app = make_app()
    app.mics = []
    monkeypatch.setattr(app.root, "after", lambda *a: None)
    for _ in range(4):
        app._watch_audio()
    assert not opened
    found.append((5, "USB mixer"))
    for _ in range(4):  # within a few checks, not only on the first
        app._watch_audio()
    assert opened == [5]


def test_a_thousand_detections_and_clears_in_a_row(make_app):
    """A long service: nothing piles up and the screen still answers."""
    app = make_app()
    for verse in range(1, 177):
        type_verse(app, f"Psalm 119 {verse}")
        if verse % 5 == 0:
            app.clear_live()
        if verse % 7 == 0:
            app.undo_live()
        if verse % 11 == 0:
            app.wrong_verse()
    assert len(app.history) <= app_module.MAX_HISTORY and len(app.undo_stack) <= app_module.MAX_UNDO
    type_verse(app, "John 11 35")
    assert app.projector.body == "Jesus wept."


def test_missing_verse_in_second_language_and_unknown_reference(make_app):
    app = make_app()
    type_verse(app, "John 3 16")
    type_verse(app, "Hezekiah 99 99")
    type_verse(app, "John 99 99")
    type_verse(app, "!!!")
    assert app.live_ref == Ref("John", 3, 16)


def test_logging_starts_even_when_the_log_folder_cannot_be_written(tmp_path, monkeypatch):
    blocker = tmp_path / "logs"
    blocker.write_text("a file where the folder should be")
    monkeypatch.setattr(reliability, "LOG_DIR", blocker)
    root = logging.getLogger()
    before = root.handlers[:]
    try:
        root.handlers = []
        reliability.setup_logging()  # must not raise
        logging.info("still running")
    finally:
        root.handlers = before


def test_ready_check_window_opens_and_reports(make_app):
    app = make_app()
    app.show_ready()
    app.root.update()
    words = app.ready_head.cget("text")
    assert words in ("READY FOR SERVICE", "ALMOST READY: CHECK THE YELLOW LINES", "NOT READY: FIX THE RED LINES")
    assert len(app.ready_rows.winfo_children()) >= 15
    app._ready_test_verse()
    app.root.update()
    assert app.live_ref == Ref("John", 3, 16)
    app.show_ready()  # pressing F5 twice must not open two windows
    app._refresh_ready()
    app.ready_window.destroy()
    app._refresh_ready()
    app.root.update()


def test_bible_update_at_start_survives_a_failed_build(monkeypatch):
    monkeypatch.setattr(app_module, "bibles_changed", lambda: True)

    def fail():
        raise RuntimeError("disk full")

    monkeypatch.setattr(app_module, "build_bibles", fail)
    app_module.refresh_bibles()  # must not raise; the old database stays in use
    app_module._kept.clear()
    gc.collect()
