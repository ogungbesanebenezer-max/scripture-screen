"""Second full screen, stage screen and livestream page (2026-11-21).

A church with a projector AND a TV gets the same picture on both; the stage and OBS pages follow the projector.
The app tests need a screen (xvfb-run is fine), like test_media.
"""
import json
import threading
import time
import urllib.error
import urllib.request

import pytest
from PIL import Image

from verse_screen import app as app_module
from verse_screen import outputs as outputs_module
from verse_screen.bible import Ref
from verse_screen.displays import Display
from verse_screen.outputs import Outputs
from verse_screen.video import MirroredPlayer

from tests.test_media import make_app, needs_screen, poster_live  # noqa: F401  (fixture)


# ---- the pages (no screen needed) ----
@pytest.fixture
def served():
    outputs = Outputs("secret-key")
    assert outputs.start()
    yield outputs
    outputs.stop()


def get(outputs, path, key="secret-key"):
    with urllib.request.urlopen(f"http://127.0.0.1:{outputs.port}{path}{'&' if '?' in path else '?'}k={key}",
                                timeout=30) as reply:
        return reply.status, reply.headers.get("Content-Type"), reply.read()


def test_pages_need_the_key(served):
    with pytest.raises(urllib.error.HTTPError) as refused:
        get(served, "/stage", key="wrong")
    assert refused.value.code == 403
    for page in ("/live", "/live?style=full", "/stage"):
        status, kind, body = get(served, page)
        assert status == 200 and kind.startswith("text/html") and b"/state?k=" in body


def test_a_waiting_page_hears_a_new_verse_at_once(served):
    first = json.loads(get(served, "/state")[2])
    assert first["mode"] == "standby"
    answer = {}

    def wait():
        answer.update(json.loads(get(served, f"/state?since={first['seq']}")[2]))

    waiting = threading.Thread(target=wait)
    started = time.monotonic()
    waiting.start()
    time.sleep(0.3)
    served.screen("text", "John 3:16 (KJV)", "For God so loved the world")
    waiting.join(10)
    assert answer["mode"] == "text" and answer["title"] == "John 3:16 (KJV)"
    assert time.monotonic() - started < 5  # not the 20 s wait


def test_poster_is_served_as_a_picture(served):
    with pytest.raises(urllib.error.HTTPError):
        get(served, "/picture")
    served.screen("picture", picture=Image.new("RGB", (4000, 2000), "blue"))
    status, kind, body = get(served, "/picture")
    assert status == 200 and kind == "image/jpeg" and body[:2] == b"\xff\xd8"
    assert get(served, "/picture")[2] == body  # made once, not once per page


def test_stage_note_and_next(served):
    served.next_up("Romans 8:28 (KJV)", "And we know")
    served.note("  5   minutes ")
    state = json.loads(get(served, "/state")[2])
    assert state["next_title"] == "Romans 8:28 (KJV)" and state["note"] == "5 minutes"


def test_a_taken_port_moves_to_the_next(monkeypatch):
    taken = Outputs("a")
    assert taken.start()
    monkeypatch.setattr(outputs_module, "PORTS", (taken.port,))
    second = Outputs("b")
    assert second.start() and second.port != taken.port
    taken.stop()
    second.stop()


# ---- the mirrored video player (no mpv needed) ----
class FakePlayer:
    def __init__(self):
        self.calls, self.muted, self.loaded, self.error = [], None, None, None
        self.position, self.paused, self.buffering = 0.0, True, False

    def load(self, item, start=0.0, play=False, loop=False):
        self.loaded, self.paused = item, not play
        self.calls.append("load")

    def play(self):
        self.paused = False

    def pause(self):
        self.paused = True

    def seek_to(self, seconds):
        self.position = seconds
        self.calls.append(("seek", round(seconds, 1)))
        return True

    def set_mute(self, muted):
        self.muted = muted

    def set_volume(self, level):
        self.calls.append(("volume", level))

    def close(self):
        self.calls.append("close")


def test_tv_video_follows_silently():
    first, second = FakePlayer(), FakePlayer()
    player = MirroredPlayer(first, second)
    assert second.muted is True
    player.load("sermon.mp4")
    player.play()
    assert first.loaded == second.loaded == "sermon.mp4" and not first.paused and not second.paused
    player.set_mute(False)
    player.set_volume(70)
    assert first.muted is False and second.muted is True and ("volume", 70) not in second.calls
    first.position, second.position = 30.0, 31.0  # drifted a second apart
    player.sync()
    assert second.position == 30.0
    first.position, second.position = 40.0, 40.2  # close enough: leave it alone
    player.sync()
    assert second.position == 40.2
    first.paused = True
    player.sync()
    assert second.paused
    player.error = None
    player.loaded = None  # media_page does this after a dropped stream
    assert first.loaded is None and second.loaded is None
    player.close()
    assert "close" in first.calls and "close" in second.calls


# ---- the real app ----
LAPTOP = Display(0, 0, 1366, 768, True)
PROJECTOR = Display(1366, 0, 1024, 768, False)
TV = Display(2390, 0, 1280, 720, False)


@pytest.fixture
def screens(monkeypatch):
    connected = [LAPTOP, PROJECTOR, TV]
    monkeypatch.setattr(app_module, "list_displays", lambda _root: list(connected))
    return connected


def verse_live(app, ref=Ref("John", 3, 16)):
    app._set_preview(ref, "typed", "KJV")
    app.push_live()
    app.root.update()


@needs_screen
def test_projector_and_tv_show_the_same(make_app, screens, tmp_path):  # noqa: F811
    app = make_app(json.dumps({"display": PROJECTOR.label, "second_display": TV.label}))
    tv = app.projector.mirror
    assert tv is not None and tv.window.winfo_exists()
    verse_live(app)
    assert tv.body == app.projector.body and tv.title == "John 3:16 (KJV)"
    app.black_screen()
    app.root.update()
    assert tv.blackout and app.outputs.state["mode"] == "black"
    poster_live(app, tmp_path)
    assert tv.picture is app.projector.picture is not None
    app.clear_live()
    app.root.update()
    assert tv.picture is None and tv.body == ""


@needs_screen
def test_tv_cable_out_and_back_in(make_app, screens):  # noqa: F811
    app = make_app(json.dumps({"display": PROJECTOR.label, "second_display": TV.label}))
    verse_live(app)
    screens.remove(TV)
    app._refresh_displays()
    assert app.projector.mirror is None and app.projector.body  # the projector carries on
    screens.append(TV)
    app._refresh_displays()
    app.root.update()
    assert app.projector.mirror.title == "John 3:16 (KJV)"  # back with what is on screen now


@needs_screen
def test_tv_is_never_the_projector_itself(make_app, screens):  # noqa: F811
    app = make_app(json.dumps({"display": PROJECTOR.label, "second_display": PROJECTOR.label}))
    assert app.projector.mirror is None


@needs_screen
def test_picking_the_tv_in_the_screens_window(make_app, screens):  # noqa: F811
    app = make_app(json.dumps({"display": PROJECTOR.label}))
    assert app.projector.mirror is None
    app.show_screens()
    app.root.update()
    app.second_box.set(TV.label)
    app._second_picked()
    app.root.update()
    assert app.projector.mirror is not None and app.settings["second_display"] == TV.label
    app.second_box.set("Off (projector only)")
    app._second_picked()
    assert app.projector.mirror is None and app.settings["second_display"] == ""


@needs_screen
def test_stage_follows_live_and_next(make_app):  # noqa: F811
    app = make_app()
    verse_live(app)
    state = app.outputs.state
    assert state["mode"] == "text" and state["title"] == "John 3:16 (KJV)" and state["next_title"] == ""
    app._set_preview(Ref("Romans", 8, 28), "typed", "KJV")
    assert app.outputs.state["next_title"] == "Romans 8:28 (KJV)"
    app.push_live()
    app.root.update()
    assert app.outputs.state["title"] == "Romans 8:28 (KJV)" and app.outputs.state["next_title"] == ""
    app.show_screens()
    app.root.update()
    app.send_stage_note("Wrap up")
    assert app.outputs.state["note"] == "Wrap up"
    app.clear_live()
    assert app.outputs.state["mode"] == "standby"


TWO_SCREEN_VIDEO_SCRIPT = r"""
import json, sys, time
from pathlib import Path
from types import SimpleNamespace
from verse_screen import app as app_module, media as media_module
from verse_screen.app import App
from verse_screen.displays import Display
from verse_screen.video import MirroredPlayer
tmp = Path(sys.argv[2])
laptop, projector, tv = (Display(0, 0, 800, 600, True), Display(800, 0, 640, 360, False),
                         Display(1440, 0, 640, 360, False))
app_module.list_displays = lambda _root: [laptop, projector, tv]
app_module.SETTINGS_PATH = tmp / "settings.json"
options = {"audio_output": "null"} if sys.platform == "win32" else {"video_output": "x11", "audio_output": "null"}
(tmp / "settings.json").write_text(json.dumps({"video_player_options": options, "display": projector.label,
                                               "second_display": tv.label}))
media_module.MEDIA_DIR = tmp / "media"
App._load_models = lambda self: None
app_module.PhoneMic.start = lambda self: False
app = App(SimpleNamespace(model=None, simulate=None))
app.root.update()
assert app.projector.mirror is not None
item = app.media.add(sys.argv[1], "video")
app._show_media(item)
app.push_media()


def run_for(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.root.update()
        time.sleep(0.05)


deadline = time.monotonic() + 10
while time.monotonic() < deadline and app.player.position < 2:
    run_for(0.1)
player = app.player
assert isinstance(player, MirroredPlayer), type(player)
assert app.projector.mirror.video_shown and player.second.mpv.mute and not player.first.mpv.mute
run_for(1)
assert abs(player.first.position - player.second.position) < 0.6, (player.first.position, player.second.position)
app._video_do("skip", 10)
run_for(1.2)
assert player.first.position >= 10 and abs(player.first.position - player.second.position) < 0.6, (
    player.first.position, player.second.position)
app._video_do("toggle")
run_for(0.6)
assert player.first.paused and player.second.paused
app.clear_live()
run_for(0.3)
assert not app.projector.video_shown and not app.projector.mirror.video_shown
app.quit()
print("TWO SCREEN VIDEO OK")
"""


def _real_player_available():
    from tests.test_media import _real_player_available as available
    return available()


@pytest.mark.skipif(not _real_player_available(), reason="libmpv not installed here")
def test_real_video_on_projector_and_tv(tmp_path):
    """Own process, like test_media's real player test."""
    import os
    import shutil
    import subprocess
    import sys
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg needed to make a test clip")
    clip = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25",
                    "-t", "30", "-pix_fmt", "yuv420p", str(clip)], check=True)
    run = subprocess.run([sys.executable, "-c", TWO_SCREEN_VIDEO_SCRIPT, str(clip), str(tmp_path)],
                         capture_output=True, text=True, timeout=90, cwd=os.path.dirname(os.path.dirname(__file__)))
    assert "TWO SCREEN VIDEO OK" in run.stdout, run.stderr[-3000:]
