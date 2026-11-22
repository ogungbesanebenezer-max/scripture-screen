"""Added 2026-11-12: the ready check, sleep detection, automatic Bible updating and the extra Bibles."""
import logging
import time
from types import SimpleNamespace

from verse_screen import app as app_module
from verse_screen import database, ready
from verse_screen.app import App
from verse_screen.bible import Library, Ref
from verse_screen.detector import Detector
from verse_screen.references import ReferenceFinder
from verse_screen.voice import requested_translation

GOOD = dict(engine_loaded=True, engine_error=None, listening=True, input_name="Mixer", phone_on=False,
            phone_running=True, peak=0.05, heard_ago=4.0, screens=2, screen_name="External screen", bible="KJV",
            bible_ok=True, second="", plugged=True, battery=90, free_mb=5000, auto=True)


def states(**changes):
    lines = ready.check(**{**GOOD, **changes})
    return {line.name: line.state for line in lines}, ready.verdict(lines)


def test_everything_working_says_ready():
    found, (state, words) = states()
    assert set(found.values()) == {"ok"} and state == "ok" and words == "READY FOR SERVICE"


def test_each_fault_is_named():
    assert states(engine_error="Speech engine failed: x")[0]["Speech engine"] == "bad"
    assert states(engine_loaded=False)[0]["Speech engine"] == "warn"
    assert states(listening=False)[0]["Speech engine"] == "bad"
    assert states(input_name="")[0]["Sound input"] == "bad"
    assert states(input_name="", phone_on=True)[0]["Sound input"] == "ok"
    assert states(peak=0.0)[0]["Sound input"] == "warn"
    assert states(heard_ago=None)[0]["Words heard"] == "warn"
    assert states(screens=1)[0]["TV / projector"] == "bad"
    assert states(bible_ok=False)[0]["Bible"] == "bad"
    assert states(plugged=False)[0]["Power"] == "warn"
    assert "Power" not in states(plugged=None, battery=None)[0]  # a desktop or Linux: nothing to say
    assert states(free_mb=50)[0]["Disk space"] == "warn"
    assert states(auto=False)[0]["AUTO"] == "warn"
    assert states(screens=1)[1][0] == "bad" and states(plugged=False)[1][0] == "warn"


def test_database_notices_a_bible_file_added_or_removed(tmp_path):
    folder, db = tmp_path / "bibles", tmp_path / "test.db"
    folder.mkdir()
    (folder / "AAA.json").write_text('{"code": "AAA", "name": "A", "books": {"John": {"3": {"16": "text one"}}}}')
    kind = dict(directories=(folder,), vectors_path=tmp_path / "none.npy")
    assert database.is_stale(db, **kind)
    database.build(db, **kind)
    assert not database.is_stale(db, **kind)
    (folder / "BBB.json").write_text('{"code": "BBB", "name": "B", "books": {"John": {"3": {"16": "text two"}}}}')
    assert database.is_stale(db, **kind)
    database.build(db, **kind)
    assert not database.is_stale(db, **kind)
    assert Library(db).text("BBB", Ref("John", 3, 16)) == "text two"
    (folder / "BBB.json").unlink()
    assert database.is_stale(db, **kind)


def test_a_failed_rebuild_keeps_the_old_database(tmp_path):
    folder, db = tmp_path / "bibles", tmp_path / "test.db"
    folder.mkdir()
    (folder / "AAA.json").write_text('{"code": "AAA", "name": "A", "books": {"John": {"3": {"16": "text one"}}}}')
    kind = dict(directories=(folder,), vectors_path=tmp_path / "none.npy")
    database.build(db, **kind)
    (folder / "AAA.json").write_text("{damaged")
    try:
        database.build(db, **kind)
    except RuntimeError:
        pass
    assert Library(db).text("AAA", Ref("John", 3, 16)) == "text one"


def test_new_bibles_can_be_asked_for_by_name():
    assert requested_translation("read it in the Berean Standard Bible") == "BSB"
    assert requested_translation("let us see the World English version") == "WEB"
    assert requested_translation("give me that in the New King James") == "NKJV"
    assert requested_translation("you can find it on the web") is None
    assert requested_translation("the Bereans searched the scriptures") is None


def test_public_domain_bibles_are_complete_enough():
    library = Library()
    for code in ("BSB", "WEB"):
        assert code in library.codes
        assert library.text(code, Ref("John", 3, 16)) and library.text(code, Ref("Revelation", 22, 21))


def test_spoken_yoruba_prefers_the_churchs_own_bible_when_it_is_installed():
    library = Library()
    detector = Detector(library, ReferenceFinder(library))
    with_bm = SimpleNamespace(library=SimpleNamespace(codes=["KJV", "YOR", "BM"]))
    only_open = SimpleNamespace(library=SimpleNamespace(codes=["KJV", "YOR"]))
    assert Detector._installed_name(with_bm, "YOR") == "BM"
    assert Detector._installed_name(only_open, "YOR") == "YOR"
    assert Detector._installed_name(only_open, "BM") == "YOR"
    assert detector._installed_name("NKJV") == "NKJV" and detector._installed_name(None) is None


def test_a_verse_missing_from_one_bible_shows_the_king_james_instead_of_nothing():
    shown = []
    library = SimpleNamespace(text=lambda code, ref: None if code == "XXX" else f"{code} text")
    app = SimpleNamespace(library=library, second_code="", _set_mode=lambda *_a: None, _sermon_mark=lambda: "",
                          projector=SimpleNamespace(show=lambda title, body, second=None: shown.append((title, body))))
    app._verse = lambda ref, code: App._verse(app, ref, code)
    assert App._project(app, Ref("John", 3, 16), "XXX")
    assert shown == [("John 3:16 (KJV)", "KJV text")]


def test_waking_from_sleep_is_reported_and_does_not_restart_listening(caplog):
    said = {}
    listener = SimpleNamespace(beat=time.monotonic() - 900, stalled=lambda: False)
    app = SimpleNamespace(last_health=time.monotonic() - 900, listener=listener, audio_checks_lost=0,
                          source=SimpleNamespace(alive=lambda: True), status=SimpleNamespace(config=said.update), _watch_audio=None,
                          root=SimpleNamespace(after=lambda *a: None))
    with caplog.at_level(logging.WARNING):
        App._watch_audio(app)
    assert "asleep" in said["text"] and "15 min" in said["text"]
    assert time.monotonic() - listener.beat < 5
    assert any("asleep" in record.message for record in caplog.records)
    said.clear()
    App._watch_audio(app)  # a normal check three seconds later says nothing
    assert not said


def test_start_file_opens_the_app_without_the_black_window():
    text = (app_module.ROOT / "start.bat").read_text()
    assert "pythonw.exe" in text and "start_with_messages" in text


def test_bible_download_script_skips_what_is_there_and_ignores_unsafe_names(tmp_path, capsys):
    from scripts import get_private_bibles as fetcher

    assert fetcher.BSN_FILE.match("yoruba-text-1e5e/Bibeli_Mimo/BSN/JHN.3.BMY/JHN.3.16.txt")
    assert fetcher.BSN_FILE.match("x/Bibeli_Mimo/BSN/1CO.15.BMY/1CO.15.51+1CO.15.52.txt")
    assert not fetcher.BSN_FILE.match("x/Bibeli_Mimo/BSN/../../evil.txt")
    assert not fetcher.BSN_FILE.match("x/Bibeli_Mimo/Biblica/JHN.3.BMY/JHN.3.16.txt")
    (tmp_path / "NKJV.json").write_text("{}")
    (tmp_path / "BM.json").write_text("{}")
    assert fetcher.main([str(tmp_path)]) == 0  # both there already: nothing is downloaded
    assert "already here" in capsys.readouterr().out

    def broken(_work, _output):
        raise OSError("no internet")

    fetcher.TRIES, fetcher.time = 1, SimpleNamespace(sleep=lambda _s: None)
    assert fetcher.get("NKJV", broken, tmp_path / "new", again=False) is False  # a failure never raises
