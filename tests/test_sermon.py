"""SERMON TEST (2026-11-18): a recorded sermon speaks instead of the microphone, and scripts/sermon_check.py
compares the log with the sermon's captions."""
import gc
import logging
import sys
import time
import tkinter as tk
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from verse_screen import app as app_module
from verse_screen import sermon_page
from verse_screen.app import App
from verse_screen.bible import Library
from verse_screen.references import ReferenceFinder
from verse_screen.sermon_test import SAMPLE_RATE, SermonAudio, SermonFeeder

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import sermon_check  # noqa: E402


def _vtt(path, timed_words):
    """Captions in YouTube's word-timed style: [(seconds, word)]."""
    def stamp(t):
        return f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{t % 60:06.3f}"

    lines = ["WEBVTT", "Kind: captions", "Language: en", ""]
    for i in range(0, len(timed_words), 6):
        group = timed_words[i:i + 6]
        start, end = group[0][0], group[-1][0] + 0.5
        tail = "".join(f"<{stamp(t)}><c> {w}</c>" for t, w in group[1:])
        lines += [f"{stamp(start)} --> {stamp(end)} align:start position:0%", group[0][1] + tail, ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _spoken(start, text, gap=0.4):
    return [(start + i * gap, w) for i, w in enumerate(text.split())]


def _log(path, lines):
    path.write_text("\n".join(f"2026-11-18 10:00:{i % 60:02d},000 INFO {x}" for i, x in enumerate(lines)),
                    encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def library():
    return Library()


def test_log_runs_keep_only_marked_lines(tmp_path):
    log = _log(tmp_path / "app.log", [
        "Heard: before the test -> John 3:16",
        "Sermon test started: First | abcdefghijk",
        "[sermon 0:10] Heard: turn to John 3 verse 16 -> John 3:16",
        "[sermon 0:11] On screen: John 3:16 (KJV)",
        "[sermon 1:02:03] Heard once, nothing against it -> Romans 8:28",
        "Sermon test stopped at 1:02:05: First",
        "Heard: after the test -> Psalms 23:1",
        "Sermon test started: Second | x | lmnopqrstuv",
        "[sermon 0:05] Heard: grace -> -",
    ])
    first, second = sermon_check.read_runs(log)
    assert (first.name, first.source) == ("First", "abcdefghijk")
    assert first.heard == [(10, "turn to John 3 verse 16", ["John 3:16"]), (3723, "", ["Romans 8:28"])]
    assert first.shown == [(11, "John 3:16 (KJV)")]
    assert (second.name, second.source, second.heard) == ("Second | x", "lmnopqrstuv", [(5, "grace", [])])


def test_check_finds_caught_late_missed_and_extra(tmp_path, library):
    words = (_spoken(5, "good morning church please turn with me to John chapter 3 verse 16 for God so loved")
             + _spoken(60, "and the word of God says in Romans chapter 8 verse 28 that all things work together")
             + _spoken(120, "let us pray now and give thanks to the Lord for his goodness and mercy"))
    captions = _vtt(tmp_path / "abcdefghijk.en-orig.vtt", words)
    run = sermon_check.Run("Test", "abcdefghijk",
                           heard=[(10, "turn with me", []), (18, "John chapter 3 verse 16", ["John 3:16"]),
                                  (66, "Roman", []), (75, "things work together", []),
                                  (125, "let us pray", ["Psalms 23:1"])],
                           shown=[(19, "John 3:16 (KJV)")])
    lines, totals = sermon_check.check(run, captions, library, ReferenceFinder(library))
    report = "\n".join(lines)
    assert (totals["caught"], totals["missed"], totals["extra"]) == (1, 1, 1), report
    assert "John 3:16" in report.split("MISSED")[0] and "Romans 8:28" in report.split("MISSED")[1]
    assert "Psalms 23:1" in report.split("EXTRA")[1] and totals["extra_on_screen"] == 0
    assert "John 3:16" in report.split("CAUGHT")[1].split("MISSED")[0]


def test_parts_the_test_skipped_are_not_counted(tmp_path, library):
    words = _spoken(5, "turn with me to John chapter 3 verse 16") + _spoken(600, "now Romans chapter 8 verse 28")
    captions = _vtt(tmp_path / "abcdefghijk.en.vtt", words)
    run = sermon_check.Run("Test", "abcdefghijk", heard=[(12, "John 3 16", ["John 3:16"])])
    lines, totals = sermon_check.check(run, captions, library, ReferenceFinder(library))
    assert (totals["caught"], totals["missed"]) == (1, 0)
    assert any("skipped past them): 1" in x for x in lines)


def test_split_reference_counts_once(library):
    """'Judges chapter 15 from verse' ... '9 to 13' cut in two by the 8-second pieces is one passage."""
    words = _spoken(30, "look at the story in Judges chapter 15 from verse 9 to 13 the men of Judah came", gap=0.6)
    spoken = sermon_check.spoken_verses(words, library, ReferenceFinder(library))
    assert len(spoken) == 1 and any(x.startswith("Judges 15") for x in spoken[0][1])


def test_captions_found_by_video_id(tmp_path, monkeypatch):
    folder = tmp_path / "sermons" / "01_pastor"
    folder.mkdir(parents=True)
    (folder / "abcdefghijk.en-orig.vtt").write_text("WEBVTT\n")
    monkeypatch.setattr(sermon_check, "SERMONS", tmp_path / "sermons")
    for source in ("abcdefghijk", "https://www.youtube.com/watch?v=abcdefghijk&t=5",
                   "https://youtu.be/abcdefghijk"):
        assert sermon_check.find_captions(sermon_check.Run("x", source)) == folder / "abcdefghijk.en-orig.vtt"


class FakeRing:
    def __init__(self):
        self.added = []

    def add(self, samples):
        self.added.append(samples)


class FakeAudio:
    error, done = None, True

    def __init__(self, seconds):
        self.seconds = seconds
        self.data = bytearray(np.arange(int(seconds * SAMPLE_RATE), dtype=np.int16).tobytes())

    def samples(self, start, end):
        return np.frombuffer(bytes(self.data[start * 2:end * 2]), dtype=np.int16).astype(np.float32)

    def stop(self):
        pass


class Clock:
    position, paused = 0.0, False


def _wait(condition, seconds=3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end and not condition():
        time.sleep(0.05)
    return condition()


def test_feeder_follows_the_player_pause_and_seek():
    ring, player = FakeRing(), Clock()
    feeder = SermonFeeder(ring, FakeAudio(120), player)
    try:
        player.position = 2.0
        assert _wait(lambda: feeder.fed == 2 * SAMPLE_RATE)
        assert sum(len(x) for x in ring.added) == 2 * SAMPLE_RATE
        assert ring.added[0][0] == 0  # from the start of the sermon, in order
        player.paused, player.position = True, 2.5
        time.sleep(0.5)
        assert feeder.fed == 2 * SAMPLE_RATE  # paused: nothing new
        player.paused, player.position = False, 60.0  # the operator skipped ahead
        assert _wait(lambda: feeder.fed == 60 * SAMPLE_RATE)
        assert sum(len(x) for x in ring.added) == 2 * SAMPLE_RATE  # the skipped minute is not heard
        player.position = 61.0
        assert _wait(lambda: feeder.fed == 61 * SAMPLE_RATE)
        assert ring.added[-1][-1] == np.arange(61 * SAMPLE_RATE, dtype=np.int16)[-1]
        assert feeder.position == 61.0
    finally:
        feeder.stop()


def test_sermon_audio_reads_a_recording(tmp_path):
    path = tmp_path / "sermon.wav"
    with wave.open(str(path), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(44100)
        tone = (np.sin(np.arange(44100 * 2) / 44100 * 2 * np.pi * 220) * 8000).astype(np.int16)
        out.writeframes(np.repeat(tone, 2).tobytes())
    audio = SermonAudio(path)
    assert _wait(lambda: audio.done, 10)
    assert audio.error is None and abs(audio.seconds - 2.0) < 0.05
    loud = audio.samples(0, SAMPLE_RATE)
    assert len(loud) == SAMPLE_RATE and 0.15 < np.abs(loud).max() < 0.3


def test_unreadable_recording_reports_instead_of_crashing(tmp_path):
    path = tmp_path / "broken.mp3"
    path.write_bytes(b"not a sermon")
    audio = SermonAudio(path)
    assert _wait(lambda: audio.done, 10)
    assert audio.error


# ---- in the app ----

def _has_screen():
    try:
        tk.Tk().destroy()
        gc.collect()
        return True
    except tk.TclError:
        return False


needs_screen = pytest.mark.skipif(not _has_screen(), reason="no screen available")


class FakePlayer:
    def __init__(self, window_id, **options):
        self.window_id, self.position, self.paused, self.duration, self.closed = window_id, 0.0, True, 300.0, False

    def load(self, item, play=False, **_kw):
        self.loaded, self.paused = item, not play

    def toggle(self):
        self.paused = not self.paused

    def skip(self, seconds):
        self.position += seconds

    def seek_to(self, seconds):
        self.position = seconds

    def close(self):
        self.closed = True


class FakeSource:
    def __init__(self):
        self.stopped = False

    def alive(self):
        return not self.stopped

    def stop(self):
        self.stopped = True


@pytest.fixture
def make_app(tmp_path, monkeypatch):
    made = []
    monkeypatch.setattr(app_module, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(App, "_load_models", lambda self: None)
    monkeypatch.setattr(app_module.PhoneMic, "start", lambda self: False)
    monkeypatch.setattr(sermon_page.video_module, "Player", FakePlayer)
    monkeypatch.setattr(sermon_page, "SermonAudio", lambda source, headers=None: FakeAudio(300))

    def build():
        app = App(SimpleNamespace(model=None, simulate=None))
        app.root.update()
        made.append(app)
        return app

    yield build
    for app in made:
        app.stop_sermon_test(quiet=True)
        try:
            app.root.destroy()
        except tk.TclError:
            pass
    made.clear()
    gc.collect()


@needs_screen
def test_sermon_speaks_instead_of_the_mic_and_the_mic_comes_back(make_app, tmp_path, monkeypatch, caplog):
    app = make_app()
    mic = app.source = FakeSource()
    restarted = []
    app.show_sermon_test()
    app.start_sermon_test(tmp_path / "sermon.opus", "Pastor: Sunday", source_id="abcdefghijk")
    app.root.update()
    assert mic.stopped and app.source is app.sermon_feeder and not app.sermon_player.paused
    app._start_audio()  # the speech engine finishing its load must not take the sermon off
    assert app.source is app.sermon_feeder
    app.transcriber = SimpleNamespace(hearing=None)  # no speech engine in this test
    app.sermon_player.position = 750.0
    app.sermon_feeder.fed = 750 * SAMPLE_RATE
    with caplog.at_level(logging.INFO):
        app._apply("turn to John 3 verse 16", 1.0, SimpleNamespace(found=[], translation=None,
                                                                   missing_translation=None))
    assert "[sermon 12:30] Heard: turn to John 3 verse 16" in caplog.text
    app._sermon_skip(10)
    assert app.sermon_player.position == 760.0
    monkeypatch.setattr(App, "_start_audio", lambda self, remember=False: restarted.append(True))
    player = app.sermon_player
    app.stop_sermon_test()
    assert player.closed and app.sermon_feeder is None and restarted == [True]
    assert app._sermon_mark() == ""


@needs_screen
def test_closing_the_window_ends_the_test(make_app, tmp_path, monkeypatch):
    app = make_app()
    app.show_sermon_test()
    app.start_sermon_test(tmp_path / "sermon.opus", "Pastor: Sunday", source_id="abcdefghijk")
    monkeypatch.setattr(App, "_start_audio", lambda self, remember=False: None)
    app._close_sermon_window()
    app.root.update()
    assert app.sermon_window is None and app.sermon_feeder is None
    app.show_sermon_test()  # opens again cleanly
    app.root.update()
    assert app.sermon_window.winfo_exists()


def test_checked_answer_key_replaces_the_captions(tmp_path, library, monkeypatch):
    folder = tmp_path / "sermons" / "01_pastor"
    folder.mkdir(parents=True)
    captions = _vtt(folder / "abcdefghijk.en-orig.vtt", _spoken(5, "turn to Jeremiah 3 verse 3"))
    (folder / "abcdefghijk.key.tsv").write_text(
        "# time\tstatus\treference\n0:05\tREAD\tJeremiah 33:3\t0.9\n0:30\tDROP\tJeremiah 3:3\n"
        "1:00\tNAMED\tJohn 14:14 / John 14:13-14\n", encoding="utf-8")
    monkeypatch.setattr(sermon_check, "SERMONS", tmp_path / "sermons")
    key = sermon_check.find_key(captions)
    assert key == folder / "abcdefghijk.key.tsv"
    run = sermon_check.Run("Test", "abcdefghijk", heard=[(9, "Jeremiah 33 3", ["Jeremiah 33:3"]), (40, "x", []),
                                                          (70, "pray", [])])
    lines, totals = sermon_check.check(run, captions, library, ReferenceFinder(library), key)
    assert (totals["caught"], totals["missed"]) == (1, 1)
    assert "John 14:14 / John 14:13-14" in "\n".join(lines).split("MISSED")[1]
