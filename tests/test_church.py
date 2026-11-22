"""OUR CHURCH: name, logo and colour set once, used on the projector, the TV and the livestream."""
import json
import urllib.request
from pathlib import Path

from PIL import Image

from tests.test_media import make_app, needs_screen  # noqa: F401
from verse_screen import church_page
from verse_screen.app import load_settings  # noqa: F401
from verse_screen.bible import Ref
from verse_screen.detector import Found


def show_verse(app):
    app._add_detection(Found(Ref("John", 3, 16), "typed", translation="KJV", triggered=True))
    app.root.update()
    if not app.projector.body:
        app._space()
    for _ in range(40):
        app.root.update()
        app.root.after(25)
    assert app.projector.body, "the verse did not go up"


def logo(path, color=(200, 30, 30, 255)):
    image = Image.new("RGBA", (300, 150), (0, 0, 0, 0))
    image.paste(Image.new("RGBA", (200, 100), color), (50, 25))
    image.save(path)
    return path


@needs_screen
def test_church_settings_reach_every_screen(make_app, tmp_path, monkeypatch):
    app = make_app()
    monkeypatch.setattr(church_page.filedialog, "askopenfilename", lambda **_k: str(logo(tmp_path / "Our Logo.PNG")))
    app.show_church()
    app.church_name_entry.insert(0, "  Grace   Chapel ")
    app.church_choose["color"] = "#7fb2ff"
    app.church_corner.set("Top left")
    [b for b in app.church_window.winfo_children()]  # built
    church_page.filedialog.askopenfilename()  # what CHOOSE LOGO would get
    app.church_choose["logo"] = str(tmp_path / "Our Logo.PNG")
    app.church_save()
    saved = app.settings
    assert saved["church_name"] == "Grace Chapel" and saved["church_color"] == "#7fb2ff"
    assert saved["church_logo_corner"] == "top-left"
    assert Path(saved["church_logo"]).parts[-2:] == ("church", "logo.png")  # copied: the USB stick can go
    assert "Grace Chapel" in app.root.title()
    assert app.projector.accent == "#7fb2ff" and app.projector.mark is not None
    assert app.projector.source is not None  # the logo is the standby picture
    show_verse(app)
    assert app.projector.mark_item is not None  # faint logo in the corner of the verse
    state = app.outputs.snapshot()
    assert state["church"] == "Grace Chapel" and state["accent"] == "#7fb2ff" and state["logo"]
    with urllib.request.urlopen(app.outputs.link("logo")) as reply:
        assert reply.headers["Content-Type"] == "image/png" and reply.read()[:4] == b"\x89PNG"


@needs_screen
def test_church_settings_come_back_after_restart(make_app, tmp_path):
    path = logo(tmp_path / "logo.png")
    app = make_app(json.dumps({"church_name": "Grace Chapel", "church_logo": str(path), "church_color": "#6fd39a",
                               "church_logo_corner": "bottom-right"}))
    assert app.projector.accent == "#6fd39a" and app.projector.mark_corner == "bottom-right"


@needs_screen
def test_broken_church_settings_never_stop_the_app(make_app, tmp_path):
    (tmp_path / "bad.png").write_text("not a picture")
    app = make_app(json.dumps({"church_logo": str(tmp_path / "bad.png"), "church_color": "red",
                               "church_logo_corner": "middle"}))
    assert app.projector.mark is None and app.projector.accent == "#e6b85c"
    assert "church_color" not in app.settings and "church_logo_corner" not in app.settings


@needs_screen
def test_logo_corner_off_and_removed(make_app, tmp_path):
    app = make_app(json.dumps({"church_logo": str(logo(tmp_path / "logo.png")), "church_logo_corner": "off"}))
    show_verse(app)
    assert app.projector.mark_item is None
    app.show_church()
    app.church_choose["logo"] = ""
    app.church_save()
    assert "church_logo" not in app.settings and app.projector.mark is None
