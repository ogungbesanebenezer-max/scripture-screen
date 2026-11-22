"""Media control, phases 1-2 (2026-11-17): posters and pictures share the one projector with the verses.

Load -> preview -> push live -> take off, and speech must never knock a poster off the screen by itself.
Needs a screen (xvfb-run is fine), like test_app_survives.
"""
import gc
import tkinter as tk
from types import SimpleNamespace

import pytest
from PIL import Image

from verse_screen import app as app_module
from verse_screen import media as media_module
from verse_screen import media_page
from verse_screen.app import App
from verse_screen.bible import Ref
from verse_screen.detector import Found
from verse_screen.media import MediaLibrary, load_picture


def _has_screen():
    try:
        tk.Tk().destroy()
        gc.collect()
        return True
    except tk.TclError:
        return False


def picture(path, color="red", size=(320, 180), mode="RGB"):
    Image.new(mode, size, color).save(path)
    return path


# ---- library (no screen needed) ----
def test_library_keeps_a_copy_and_finds_it_again(tmp_path):
    source = picture(tmp_path / "Sunday Programme.png")
    library = MediaLibrary(tmp_path / "media")
    item = library.add(source, "announcement")
    source.unlink()  # the USB stick went home
    assert item.path.exists() and item.title == "Sunday Programme"
    library.describe(item.id, "Sun 23 Nov", "Thanksgiving service")
    library.close()

    again = MediaLibrary(tmp_path / "media")  # next Sunday
    [found] = again.items("announcement", "thanksgiving")
    assert (found.title, found.event_date) == ("Sunday Programme", "Sun 23 Nov")
    assert again.items("image") == []
    again.rename(found.id, "Thanksgiving")
    assert again.get(found.id).title == "Thanksgiving"
    again.delete(found.id)
    assert again.get(found.id) is None and not found.path.exists()


def test_library_refuses_what_it_cannot_show(tmp_path):
    library = MediaLibrary(tmp_path / "media")
    (tmp_path / "notes.txt").write_text("hello")
    (tmp_path / "broken.jpg").write_text("not a picture")
    for name in ("notes.txt", "broken.jpg"):
        with pytest.raises(ValueError):
            library.add(tmp_path / name, "image")
    assert library.items("image") == []


def test_see_through_pictures_go_on_black(tmp_path):
    shown = load_picture(picture(tmp_path / "logo.png", (255, 255, 255, 0), mode="RGBA"))
    assert shown.mode == "RGB" and shown.getpixel((5, 5)) == (0, 0, 0)


# ---- the real app ----
needs_screen = pytest.mark.skipif(not _has_screen(), reason="no screen available")


@pytest.fixture
def make_app(tmp_path, monkeypatch):
    made = []
    monkeypatch.setattr(app_module, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(media_module, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(App, "_load_models", lambda self: None)
    monkeypatch.setattr(app_module.PhoneMic, "start", lambda self: False)

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
    gc.collect()


def poster_live(app, tmp_path, name="Sunday Programme"):
    item = app.media.add(picture(tmp_path / f"{name}.png", "blue"), "announcement")
    app.show_page("media")
    app._fill_media_list()
    app._show_media(item)
    app.root.update()
    assert app.projector.picture is None  # preview only: the congregation sees nothing yet
    app.push_media()
    app.root.update()
    return item


def hear(app, ref, kind):
    app._add_detection(Found(ref, kind, translation="KJV", triggered=True))
    app.root.update()


@needs_screen
def test_poster_goes_live_and_comes_off(make_app, tmp_path):
    app = make_app()
    item = poster_live(app, tmp_path)
    assert app.mode == "media" and app.live_media.id == item.id
    assert app.projector.picture is not None and app.projector.body == ""
    assert "Sunday Programme" in app.live_label.cget("text")
    app.clear_live()
    app.root.update()
    assert app.mode == "standby" and app.projector.picture is None


@needs_screen
def test_speech_never_replaces_the_poster_by_itself(make_app, tmp_path):
    app = make_app()  # AUTO is on by default
    poster_live(app, tmp_path)
    hear(app, Ref("John", 3, 16), "exact")      # the choir sings a line that names a verse
    hear(app, Ref("Psalms", 23, 1), "command")   # even "media, give me Psalm 23" waits, by default
    assert app.mode == "media" and app.live_ref is None
    assert app.preview_ref == Ref("Psalms", 23, 1)  # ready for the operator
    assert app.page == "scripture"
    app._space()  # the operator agrees
    app.root.update()
    assert app.mode == "scripture" and app.live_ref == Ref("Psalms", 23, 1)
    assert app.projector.picture is None


@needs_screen
def test_switch_setting_lets_the_pastor_take_the_screen(make_app, tmp_path):
    app = make_app('{"verse_during_media": "switch"}')
    poster_live(app, tmp_path)
    hear(app, Ref("John", 3, 16), "exact")
    assert app.mode == "media"  # only an outright request switches
    hear(app, Ref("Psalms", 23, 1), "command")
    assert app.mode == "scripture" and app.live_ref == Ref("Psalms", 23, 1)


@needs_screen
def test_amen_does_not_clear_a_poster(make_app, tmp_path):
    app = make_app()
    poster_live(app, tmp_path)
    app._hear_while_live("amen praise the lord let us pray")
    assert app.mode == "media"


@needs_screen
def test_typed_verse_still_goes_up_over_media(make_app, tmp_path):
    app = make_app()
    poster_live(app, tmp_path)
    app.search_entry.insert(0, "John 3 16")
    app._typed_search(None)
    app.root.update()
    assert app.mode == "scripture" and app.projector.body.startswith("For God so loved")


@needs_screen
def test_black_screen_holds_and_verses_wait(make_app):
    app = make_app()
    app.black_screen()
    app.root.update()
    assert app.mode == "black" and app.projector.blackout
    hear(app, Ref("John", 3, 16), "exact")
    assert app.mode == "black" and app.live_ref is None


@needs_screen
def test_undo_after_poster_brings_the_verse_back(make_app, tmp_path):
    app = make_app()
    hear(app, Ref("John", 3, 16), "typed")
    poster_live(app, tmp_path)
    app.undo_live()
    app.root.update()
    assert app.mode == "scripture" and app.live_ref == Ref("John", 3, 16)


@needs_screen
def test_deleting_the_live_poster_takes_it_off(make_app, tmp_path, monkeypatch):
    app = make_app()
    poster_live(app, tmp_path)
    monkeypatch.setattr(app_module.messagebox, "askyesno", lambda *a, **k: True)
    app.delete_media()
    app.root.update()
    assert app.mode == "standby" and app.media.items("announcement") == []


@needs_screen
def test_standby_picture_survives_a_restart_and_a_missing_file(make_app, tmp_path):
    app = make_app()
    item = app.media.add(picture(tmp_path / "Welcome.png", "green"), "image")
    app._show_media(item)
    app.use_as_standby()
    assert app.settings["standby_image"] == str(item.path)
    item.path.unlink()  # someone tidied the folder by hand
    again = make_app()  # must still start, with the logo
    assert again.mode == "standby"


@needs_screen
def test_broken_media_library_does_not_stop_the_verses(make_app, monkeypatch):
    def broken(*_a, **_k):
        raise OSError("disk error")

    monkeypatch.setattr(app_module, "MediaLibrary", broken)
    app = make_app()
    assert app.media is None
    app.show_page("media")
    app.search_entry.insert(0, "John 3 16")
    app._typed_search(None)
    app.root.update()
    assert app.live_ref == Ref("John", 3, 16)


# ---- video (2026-11-17): files and links, controlled from the operator screen ----
class FakePlayer:
    def __init__(self, *_a, **_k):
        self.loaded, self.error, self.paused, self.position, self.duration = None, None, True, 0.0, 60.0
        self.ended = self.buffering = False
        self.buffered_ahead, self.volume, self.muted, self.closed = 0.0, 100, False, False

    def load(self, item, start=0.0, play=False, loop=False):
        self.loaded, self.paused, self.position, self.loop = item, not play, start, loop

    def set_loop(self, loop):
        self.loop = loop

    def set_audio_device(self, name):
        self.device = name

    def audio_devices(self):
        return [("auto", "Autoselect"), ("wasapi/{mixer}", "Line Out (USB Audio CODEC)"),
                ("wasapi/{hdmi}", "Projector (HDMI)")]

    def play(self):
        self.paused = False

    def pause(self):
        self.paused = True

    def toggle(self):
        self.paused = not self.paused

    def skip(self, seconds):
        self.position = max(0.0, self.position + seconds)

    def seek_to(self, seconds):
        self.position = seconds

    def restart(self):
        self.position = 0.0

    def stop(self):
        self.loaded = None

    def set_volume(self, level):
        self.volume = level

    def set_mute(self, muted):
        self.muted = muted

    def close(self):
        self.closed = True


@pytest.fixture
def fake_player(monkeypatch):
    monkeypatch.setattr(media_page.video_module, "Player", FakePlayer)


def video_file(tmp_path, name="Choir ministration.mp4"):
    path = tmp_path / name
    path.write_bytes(b"\0" * 2048)  # the fake player never reads it
    return path


def test_library_takes_videos_and_links_where_they_belong(tmp_path):
    library = MediaLibrary(tmp_path / "media")
    item = library.add(video_file(tmp_path), "video")
    assert item.is_video and item.path.exists()
    poster = library.add(video_file(tmp_path, "Convention promo.mp4"), "announcement")
    assert poster.is_video
    with pytest.raises(ValueError):
        library.add(video_file(tmp_path, "clip.mp4"), "image")
    link = library.add_link("https://www.youtube.com/watch?v=abc", "Sunday Service HQ")
    assert link.is_video and link.url and not link.path.name
    library.delete(link.id)  # a link has no file to remove
    assert [i.title for i in library.items("video")] == ["Choir ministration"]


def test_old_library_gains_the_link_column(tmp_path):
    import sqlite3
    folder = tmp_path / "media"
    (folder / "files").mkdir(parents=True)
    old = sqlite3.connect(folder / "media.db")
    old.executescript("CREATE TABLE media_items (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL, "
                      "file_name TEXT NOT NULL, original_name TEXT NOT NULL DEFAULT '', added TEXT NOT NULL);"
                      "INSERT INTO media_items VALUES (1, 'image', 'Logo', 'x.png', 'x.png', '2026-11-17 10:00');")
    old.commit()
    old.close()
    [logo] = MediaLibrary(folder).items("image")
    assert logo.title == "Logo" and logo.url == ""


@needs_screen
def test_video_file_plays_is_controlled_and_comes_off(make_app, tmp_path, fake_player):
    app = make_app()
    item = app.media.add(video_file(tmp_path), "video")
    app.show_page("media")
    app._pick_kind("video")
    app._show_media(item)
    app.get_ready()  # loads out of sight
    assert app.player.loaded == item.path and app.player.paused and app.mode == "standby"
    assert not app.projector.video_shown
    app._space()  # push to live
    app.root.update()
    assert app.mode == "video" and app.projector.video_shown and not app.player.paused
    app._video_do("skip", 10)
    app._video_do("toggle")
    assert app.player.position == 10 and app.player.paused
    app._update_video_controls()  # the 0.4 s refresh gives the seek bar the video's length
    app.seek.set(42)
    app._seek_released(None)
    assert app.player.position == 42
    app._video_do("set_volume", 35)
    assert app.player.volume == 35
    app._update_video_controls()
    assert "0:42" in app.video_time.cget("text")
    hear(app, Ref("John", 3, 16), "exact")  # the video's own audio names a verse
    assert app.mode == "video"
    app.stop_video()
    app.root.update()
    assert app.mode == "standby" and not app.projector.video_shown and app.player.loaded is None


@needs_screen
def test_verse_over_video_pauses_it_and_it_resumes_where_it_was(make_app, tmp_path, fake_player):
    app = make_app()
    item = app.media.add(video_file(tmp_path), "video")
    app._show_media(item)
    app.push_media()
    app.player.position = 125.0
    type_verse_media(app, "John 3 16")
    assert app.mode == "scripture" and app.player.paused and not app.projector.video_shown
    app._show_media(item)
    app.push_media()
    assert app.mode == "video" and app.player.position == 125.0 and not app.player.paused


def type_verse_media(app, text):
    app.search_entry.delete(0, "end")
    app.search_entry.insert(0, text)
    app._typed_search(None)
    app.root.update()


@needs_screen
def test_pasted_link_is_found_saved_and_played(make_app, fake_player, monkeypatch):
    from verse_screen.video import Stream
    looked_up = []

    def fake_find(url):
        looked_up.append(url)
        return Stream(url=url, title="RCCG Holy Ghost Service", duration=5400, video="https://cdn/v", audio="https://cdn/a")

    monkeypatch.setattr(app_module, "find_stream", fake_find)
    monkeypatch.setattr(media_page.simpledialog, "askstring", lambda *a, **k: "youtu.be/abc123")
    app = make_app()
    app.show_page("media")
    app._pick_kind("video")
    app.paste_link()
    for _ in range(100):
        app.root.update()
        if app.media_selected:
            break
        app.root.after(20)
    assert looked_up == ["https://youtu.be/abc123"]
    [saved] = app.media.items("video")
    assert saved.title == "RCCG Holy Ghost Service" and saved.url == "https://youtu.be/abc123"
    assert app.player.loaded.video == "https://cdn/v" and app.player.paused  # ready, not yet live
    app.push_media()
    assert app.mode == "video"


@needs_screen
def test_link_that_cannot_play_says_why(make_app, fake_player, monkeypatch):
    from verse_screen.video import VideoError
    said = []

    def refuse(_url):
        raise VideoError("This video is private. Ask headquarters to make it Public or Unlisted.")

    monkeypatch.setattr(app_module, "find_stream", refuse)
    monkeypatch.setattr(media_page.simpledialog, "askstring", lambda *a, **k: "https://youtu.be/x")
    monkeypatch.setattr(app_module.messagebox, "showwarning", lambda title, text, **k: said.append(text))
    app = make_app()
    app.show_page("media")
    app.paste_link()
    for _ in range(100):
        app.root.update()
        if said:
            break
        app.root.after(20)
    assert said and "private" in said[0] and app.mode == "standby"
    assert app.media.items("video") == []


def test_plain_reasons():
    from verse_screen.video import plain_reason
    assert "private" in plain_reason("ERROR: [youtube] x: Private video. Sign in if you've been granted access")
    assert "internet" in plain_reason("urlopen error [Errno -3] Temporary failure in name resolution")
    assert "removed" in plain_reason("ERROR: [youtube] x: Video unavailable")


def _real_player_available():
    try:
        from verse_screen import video
        video._load_mpv()
        return _has_screen()
    except Exception:
        return False


REAL_PLAYER_SCRIPT = r"""
import sys, time, tempfile
from pathlib import Path
from types import SimpleNamespace
from verse_screen import app as app_module, media as media_module
from verse_screen.app import App
tmp = Path(sys.argv[2])
app_module.SETTINGS_PATH = tmp / "settings.json"
options = {"audio_output": "null"} if sys.platform == "win32" else {"video_output": "x11", "audio_output": "null"}
import json
(tmp / "settings.json").write_text(json.dumps({"video_player_options": options}))
media_module.MEDIA_DIR = tmp / "media"
App._load_models = lambda self: None
app_module.PhoneMic.start = lambda self: False
app = App(SimpleNamespace(model=None, simulate=None))
app.root.update()
item = app.media.add(sys.argv[1], "video")
app._show_media(item)
app.push_media()
deadline = time.monotonic() + 10
while time.monotonic() < deadline and app.player.position < 1:
    app.root.update()
    time.sleep(0.05)
assert app.player.position >= 1 and app.mode == "video", (app.player.position, app.mode)
app._video_do("skip", 10)
time.sleep(0.4)
assert app.player.position >= 10, app.player.position
app.clear_live()
app.root.update()
time.sleep(0.3)
assert app.player.paused and not app.projector.video_shown
app.quit()
print("REAL PLAYER OK")
"""


@pytest.mark.skipif(not _real_player_available(), reason="libmpv not installed here")
def test_real_player_plays_a_file_inside_the_projector(tmp_path):
    """Its own process: in one Linux process, libmpv's X11 output and Tk's stall the windows of later tests."""
    import os
    import shutil
    import subprocess
    import sys
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg needed to make a test clip")
    clip = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25",
                    "-t", "20", "-pix_fmt", "yuv420p", str(clip)], check=True)
    run = subprocess.run([sys.executable, "-c", REAL_PLAYER_SCRIPT, str(clip), str(tmp_path)], capture_output=True,
                         text=True, timeout=60, cwd=os.path.dirname(os.path.dirname(__file__)))
    assert "REAL PLAYER OK" in run.stdout, run.stderr[-2000:]


@needs_screen
def test_play_on_a_picked_video_just_plays_it(make_app, tmp_path, fake_player):
    """Owner 2026-11-17: picked a video, pressed PLAY, got "No video loaded"."""
    app = make_app()
    item = app.media.add(video_file(tmp_path), "video")
    app.show_page("media")
    app._pick_kind("video")
    app._show_media(item)
    app._update_video_controls()
    assert "PICKED" in app.video_now.cget("text")
    app._video_do("toggle")
    assert app.mode == "video" and app.player.loaded == item.path and not app.player.paused
