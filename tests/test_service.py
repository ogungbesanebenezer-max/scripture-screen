"""Phases 5-8 (2026-11-17): hymns, music, the poster loop, the service order, animated GIFs, and a long random
session that checks the screen and the operator's view never disagree. Needs a screen (xvfb-run is fine)."""
import random
import tkinter as tk
from datetime import date, timedelta

import pytest
from PIL import Image

from verse_screen import app as app_module
from verse_screen import media_page
from verse_screen.bible import Ref
from verse_screen.detector import Found
from verse_screen.hymns import Hymn, Stanza, parse_lyrics, parse_openlyrics, parse_text_file, sequence
from verse_screen.media import CURRENT_ORDER, MediaLibrary

from tests.test_media import FakePlayer, make_app, needs_screen, picture, video_file  # noqa: F401

AMAZING_GRACE = """Amazing grace! how sweet the sound
That saved a wretch like me!

Chorus:
My chains are gone, I've been set free

2. 'Twas grace that taught my heart to fear,
And grace my fears relieved;

Through many dangers, toils and snares,
I have already come;"""


# ---- hymn words (no screen needed) ----
def test_stanzas_verses_and_chorus():
    stanzas = parse_lyrics(AMAZING_GRACE)
    assert [s.label for s in stanzas] == ["Verse 1", "Chorus", "Verse 2", "Verse 3"]
    assert stanzas[2].text.startswith("'Twas grace")  # the "2." is not shown on screen
    shown = sequence(Hymn(1, "Amazing Grace", stanzas=stanzas))
    assert [s.label for s in shown] == ["Verse 1", "Chorus", "Verse 2", "Chorus", "Verse 3", "Chorus"]
    assert [s.label for s in sequence(Hymn(1, "x", stanzas=stanzas), repeat_chorus=False)] == \
        ["Verse 1", "Chorus", "Verse 2", "Verse 3"]


def test_yoruba_chorus_word_and_windows_line_ends():
    stanzas = parse_lyrics("Ese kini\r\nOluwa ni\r\n\r\nEgbe:\r\nHalleluyah")
    assert [s.label for s in stanzas] == ["Verse 1", "Chorus"]


def test_text_file_with_several_hymns():
    found = parse_text_file("# 23 Amazing Grace\nLine one\n\nLine two\n\n# Blessed Assurance\nJesus is mine", "x")
    assert [(n, t, len(s)) for n, t, s in found] == [("23", "Amazing Grace", 2), ("", "Blessed Assurance", 1)]
    assert parse_text_file("Just words", "My Hymn")[0][1] == "My Hymn"


def test_openlyrics_from_other_worship_software():
    xml = b"""<?xml version='1.0' encoding='UTF-8'?>
<song xmlns="http://openlyrics.info/namespace/2009/song" version="0.8">
  <properties><titles><title>Great Is Thy Faithfulness</title></titles>
    <songbooks><songbook name="Hymns Ancient" entry="44"/></songbooks><verseOrder>v1 c v2 c</verseOrder></properties>
  <lyrics>
    <verse name="v1"><lines>Great is Thy faithfulness<br/>O God my Father</lines></verse>
    <verse name="c"><lines>Great is Thy faithfulness!<br/>Morning by morning</lines></verse>
    <verse name="v2"><lines>Summer and winter<br/>and springtime and harvest</lines></verse>
  </lyrics></song>"""
    number, title, collection, stanzas, ordered = parse_openlyrics(xml)
    assert (number, title, collection, ordered) == ("44", "Great Is Thy Faithfulness", "Hymns Ancient", True)
    assert [s.label for s in stanzas] == ["Verse 1", "Chorus", "Verse 2", "Chorus"]
    assert stanzas[0].text == "Great is Thy faithfulness\nO God my Father"


def test_hymn_library_search_by_number_and_words(tmp_path):
    library = MediaLibrary(tmp_path / "media")
    grace = library.add_hymn("Amazing Grace", parse_lyrics(AMAZING_GRACE), "23", "Hymns of Faith")
    library.add_hymn("Blessed Assurance", [Stanza("Verse 1", "Jesus is mine")], "123")
    assert [h.title for h in library.hymns("23")] == ["Amazing Grace"]
    assert [h.title for h in library.hymns("wretch")] == ["Amazing Grace"]
    library.update_hymn(grace, "Amazing Grace!", [Stanza("Verse 1", "Changed")], "23")
    assert library.get_hymn(grace).stanzas[0].text == "Changed"
    library.delete_hymn(grace)
    assert [h.title for h in library.hymns("")] == ["Blessed Assurance"]


def test_announcement_dates_and_saved_orders(tmp_path):
    library = MediaLibrary(tmp_path / "media")
    old = library.add(picture(tmp_path / "Old.png"), "announcement")
    now = library.add(picture(tmp_path / "Now.png"), "announcement")
    day = date.today()
    library.schedule(old.id, "", str(day - timedelta(days=1)))
    library.schedule(now.id, str(day - timedelta(days=3)), str(day + timedelta(days=3)))
    assert [i.title for i in library.current_announcements()] == ["Now"]
    assert library.get(old.id).schedule_note == "ended"
    with pytest.raises(ValueError):
        library.schedule(now.id, "next sunday", "")
    with pytest.raises(ValueError):
        library.schedule(now.id, "2026-12-10", "2026-12-01")
    library.save_playlist("Sunday", [("media", now.id), ("scripture", 0), ("media", old.id)])
    library.delete(old.id)
    assert library.playlist("Sunday") == [("media", now.id), ("scripture", 0)]  # deleted items drop out
    assert library.playlists() == ["Sunday"]


# ---- the real app ----
@pytest.fixture
def fake_players(monkeypatch):
    monkeypatch.setattr(media_page.video_module, "Player", FakePlayer)
    from verse_screen import service_page
    monkeypatch.setattr(service_page.video_module, "Player", FakePlayer)


def hymn_app(make_app):
    app = make_app()
    hymn_id = app.media.add_hymn("Amazing Grace", parse_lyrics(AMAZING_GRACE), "23")
    app.show_page("media")
    app._pick_kind("hymn")
    app._show_hymn(app.media.get_hymn(hymn_id))
    return app


@needs_screen
def test_hymn_goes_up_one_stanza_at_a_time(make_app):
    app = hymn_app(make_app)
    app._space()
    app.root.update()
    assert app.mode == "hymn" and app.projector.body.startswith("Amazing grace")
    assert "Verse 1" in app.projector.title
    app._arrow(1)
    assert app.projector.body.startswith("My chains") and "Chorus" in app.live_label.cget("text")
    for _ in range(10):
        app._arrow(1)
    assert app.hymn_pos == len(app.hymn_seq) - 1 and app.mode == "hymn"  # stays on the last one
    app._arrow(-1)
    assert app.projector.body.startswith("Through many")
    hear(app, Ref("John", 3, 16), "exact")  # someone sings "John three sixteen"
    assert app.mode == "hymn"
    app.clear_live()
    assert app.mode == "standby" and app.hymn_live is None


@needs_screen
def test_click_a_stanza_to_jump_and_text_size(make_app):
    app = hymn_app(make_app)
    app.hymn_preview.selection_set(4)
    app._pick_stanza(None)
    app.push_hymn()
    assert app.hymn_pos == 4 and app.projector.body.startswith("Through many")
    app.hymn_size.set("Small")
    app._hymn_options_changed()
    assert app.projector.scale == 0.65 and app.hymn_pos == 4
    app.repeat_chorus.set(False)
    app._hymn_options_changed()
    assert app.projector.body.startswith("Through many")  # same stanza after the order changed


@needs_screen
def test_auto_next_stanza(make_app):
    app = hymn_app(make_app)
    app.hymn_auto.delete(0, "end")
    app.hymn_auto.insert(0, "1")
    app._hymn_options_changed()
    app.push_hymn()
    assert app.hymn_job is not None
    app.root.after(1300, lambda: None)
    import time
    end = time.monotonic() + 2
    while time.monotonic() < end and app.hymn_pos == 0:
        app.root.update()
        time.sleep(0.05)
    assert app.hymn_pos == 1
    app.clear_live()
    assert app.hymn_job is None


@needs_screen
def test_hymn_editor_and_import(make_app, tmp_path):
    app = make_app()
    app.show_page("media")
    app._pick_kind("hymn")
    app.new_hymn()
    app.hymn_editor_fields["Title"].insert(0, "Blessed Assurance")
    app.hymn_editor_text.insert("1.0", "Blessed assurance, Jesus is mine\n\nChorus:\nThis is my story")
    app.hymn_editor_save()
    [hymn] = app.media.hymns("")
    assert hymn.title == "Blessed Assurance" and [s.label for s in hymn.stanzas] == ["Verse 1", "Chorus"]
    (tmp_path / "book.txt").write_text("# 1 Holy Holy Holy\nHoly, holy, holy\n\n# 2 Abide With Me\nAbide with me",
                                       encoding="utf-8")
    (tmp_path / "empty.txt").write_text("", encoding="utf-8")
    added, problems = app.import_hymn_files([tmp_path / "book.txt", tmp_path / "empty.txt"])
    assert added == 2 and len(problems) == 1


@needs_screen
def test_hymn_background_picture(make_app, tmp_path):
    app = make_app()
    item = app.media.add(picture(tmp_path / "Cross.png", "purple"), "image")
    app.show_page("media")
    app._pick_kind("image")
    app._show_media(item)
    app.use_as_hymn_background()
    assert app.hymn_backdrop is not None and app.settings["hymn_background"] == str(item.path)
    hymn_id = app.media.add_hymn("Abide With Me", [Stanza("Verse 1", "Abide with me")])
    app.push_hymn(app.media.get_hymn(hymn_id))
    for _ in range(30):  # the standby fades out (120 ms) before the hymn is drawn
        app.root.update()
        app.root.after(20)
    assert app.projector.backdrop is app.hymn_backdrop and app.projector.image_item
    hear(app, Ref("John", 3, 16), "typed")  # a verse after the hymn: back on black
    assert app.projector.backdrop is None


@needs_screen
def test_music_plays_under_a_poster(make_app, tmp_path, fake_players):
    app = make_app()
    song = tmp_path / "Prelude.mp3"
    song.write_bytes(b"\0" * 1024)
    track = app.media.add(song, "audio")
    app.show_page("media")
    app._pick_kind("audio")
    app._show_media(track)
    app.push_media()
    assert app.music.loaded == track.path and not app.music.paused and app.mode == "standby"
    from tests.test_media import poster_live
    poster_live(app, tmp_path)
    assert app.mode == "media" and not app.music.paused  # the poster did not stop the music
    app.music_repeat.set(True)
    app._music_do("set_loop", True)
    assert app.music.loop
    app._pick_kind("video")
    clip = app.media.add(video_file(tmp_path), "video")
    app._show_media(clip)
    app.push_media()
    assert app.mode == "video" and app.music.paused  # one sound at a time
    app.stop_music()
    assert app.music.loaded is None


@needs_screen
def test_sound_out_choice_reaches_both_players(make_app, tmp_path, fake_players):
    app = make_app()
    app._fill_sound_outputs()
    assert "Line Out (USB Audio CODEC)" in app.sound_out["values"]
    app.sound_out.set("Line Out (USB Audio CODEC)")
    app._sound_out_picked()
    assert app.music.device == "wasapi/{mixer}" and app.settings["audio_device"] == "wasapi/{mixer}"


@needs_screen
def test_poster_loop_cycles_todays_posters_and_stops_when_something_else_goes_up(make_app, tmp_path):
    app = make_app('{"loop_seconds": 2}')
    for name in ("One", "Two"):
        app.media.add(picture(tmp_path / f"{name}.png"), "announcement")
    ended = app.media.add(picture(tmp_path / "Ended.png"), "announcement")
    app.media.schedule(ended.id, "", str(date.today() - timedelta(days=1)))
    app.show_page("media")
    app.toggle_loop()
    assert app.mode == "media" and app.live_media.title == "One" and app.loop_job
    app._loop_step()
    assert app.live_media.title == "Two"
    app._loop_step()
    assert app.live_media.title == "One"  # round again, never the ended one
    type_verse(app, "John 3 16")
    assert app.loop_job is None and app.mode == "scripture"


@needs_screen
def test_animated_gif_moves_through_the_video_player(make_app, tmp_path, fake_players):
    app = make_app()
    frames = [Image.new("RGB", (64, 64), color) for color in ("red", "blue", "green")]
    gif = tmp_path / "Welcome.gif"
    frames[0].save(gif, save_all=True, append_images=frames[1:], duration=200, loop=0)
    item = app.media.add(gif, "announcement")
    assert item.is_animated
    app.show_page("media")
    app._show_media(item)
    app.push_media()
    assert app.mode == "video" and app.player.loop and app.player.loaded == item.path
    assert "(Announcement)" in app.live_label.cget("text")


@needs_screen
def test_service_order_go_next_and_survives_a_restart(make_app, tmp_path, fake_players):
    app = make_app()
    welcome = app.media.add(picture(tmp_path / "Welcome.png"), "image")
    hymn_id = app.media.add_hymn("Amazing Grace", parse_lyrics(AMAZING_GRACE))
    clip = app.media.add(video_file(tmp_path), "video")
    app.show_page("order")
    for kind, item_id in (("media", welcome.id), ("hymn", hymn_id), ("media", clip.id), ("scripture", 0)):
        app.order_list.selection_clear(0, "end")
        if app.order:
            app.order_list.selection_set(len(app.order) - 1)
        app.add_to_order((kind, item_id))
    assert [k for k, _ in app.order] == ["media", "hymn", "media", "scripture"]
    app.order_list.selection_clear(0, "end")
    app.order_list.selection_set(0)
    app._space()
    assert app.mode == "media" and app.live_media.id == welcome.id
    app.next_order()
    assert app.mode == "hymn"
    app.next_order()
    assert app.mode == "video"
    app.next_order()
    assert app.mode == "standby" and app.player.paused  # sermon time: screen ready for verses
    app.next_order()
    assert "last item" in app.order_status.cget("text")
    app.order_list.selection_clear(0, "end")
    app.order_list.selection_set(3)
    app.move_order(-1)
    assert [k for k, _ in app.order] == ["media", "hymn", "scripture", "media"]
    again = make_app()  # the laptop restarted mid-service
    assert again.order == app.order
    assert again.media.playlist(CURRENT_ORDER) == app.order


@needs_screen
def test_saved_order_for_next_week(make_app, tmp_path, monkeypatch):
    app = make_app()
    welcome = app.media.add(picture(tmp_path / "Welcome.png"), "image")
    app.show_page("order")
    app.add_to_order(("media", welcome.id))
    monkeypatch.setattr("tkinter.simpledialog.askstring", lambda *a, **k: "Sunday first service")
    app.save_order_as()
    app.order = []
    app._order_changed()
    app.saved_orders.set("Sunday first service")
    app.open_saved_order()
    assert app.order == [("media", welcome.id)]


# ---- phase 8: a long, random session ----
def hear(app, ref, kind):
    app._add_detection(Found(ref, kind, translation="KJV", triggered=True))
    app.root.update()


def type_verse(app, text):
    app.search_entry.delete(0, "end")
    app.search_entry.insert(0, text)
    app._typed_search(None)
    app.root.update()


def check_agrees(app):
    """What the operator is told is live must be what the projector shows."""
    projector = app.projector
    assert projector.video_shown == (app.mode == "video"), app.mode
    if app.mode == "scripture":
        assert app.live_ref and projector.body and projector.picture is None
    elif app.mode == "hymn":
        assert app.hymn_live and projector.body == app.hymn_seq[app.hymn_pos].text
    elif app.mode == "media":
        assert projector.picture is not None and not projector.body and app.live_media
    elif app.mode == "black":
        assert projector.blackout and not projector.body
    elif app.mode == "standby":
        assert not projector.body and projector.picture is None and not projector.blackout
    if app.mode != "scripture":
        assert app.live_ref is None
    if app.mode != "hymn":
        assert app.hymn_live is None and app.hymn_job is None


@needs_screen
def test_five_hundred_random_operator_actions(make_app, tmp_path, fake_players):
    app = make_app('{"loop_seconds": 2}')
    posters = [app.media.add(picture(tmp_path / f"P{i}.png", c), "announcement") for i, c in
               enumerate(("red", "blue"))]
    clip = app.media.add(video_file(tmp_path), "video")
    song = tmp_path / "s.mp3"
    song.write_bytes(b"\0" * 64)
    track = app.media.add(song, "audio")
    hymn = app.media.get_hymn(app.media.add_hymn("Amazing Grace", parse_lyrics(AMAZING_GRACE)))
    app.order = [("media", posters[0].id), ("hymn", hymn.id), ("media", clip.id), ("scripture", 0)]
    rng = random.Random(20261117)

    def show(item):
        app.show_page("media")
        app._pick_kind(item.kind)
        app._show_media(item)
        app.push_media()

    actions = [
        lambda: show(rng.choice(posters)),
        lambda: show(clip),
        lambda: show(track),
        lambda: app.push_hymn(hymn, start=rng.randrange(6)),
        lambda: app._arrow(rng.choice((-1, 1))),
        lambda: type_verse(app, rng.choice(("John 3 16", "Psalms 23 1", "Genesis 1 1"))),
        lambda: hear(app, Ref("Romans", 8, 28), rng.choice(("exact", "command"))),
        lambda: app._space(),
        app.clear_live, app.black_screen, app.undo_live, app.redo_live, app.stop_video, app.stop_music,
        lambda: app._video_do(rng.choice(("toggle", "restart"))),
        lambda: app._video_do("skip", rng.choice((-10, 10))),
        app.toggle_loop,
        lambda: app._loop_step() if app.loop_job else None,
        lambda: app.show_page(rng.choice(("scripture", "media", "order"))),
        lambda: app.go_order(rng.randrange(len(app.order))),
        lambda: app.next_order(rng.choice((-1, 1))) if app.order_live is not None else None,
        lambda: app._update_video_controls(),
        lambda: app._update_service_bars(),
        lambda: app.projector.window.tk.call(app.projector.window.protocol("WM_DELETE_WINDOW")),
    ]
    for step in range(500):
        rng.choice(actions)()
        app.root.update()
        check_agrees(app)
