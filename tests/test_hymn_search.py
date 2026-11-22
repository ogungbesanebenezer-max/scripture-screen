"""FIND HYMN (free library + Wikisource) and importing a church's own hymn book from Word, PDF or text."""
import zipfile
from xml.sax.saxutils import escape

import pytest

from verse_screen import hymn_search
from verse_screen.hymn_search import _stanzas_from, clean_lyrics, library, read_docx, search_library, split_hymn_book
from tests.test_media import make_app, needs_screen  # noqa: F401


def test_free_library_has_the_classics():
    titles = {h.title for h in library()}
    assert len(titles) >= 250
    for wanted in ("Amazing Grace", "Abide With Me", "Rock of Ages", "A Mighty Fortress Is Our God"):
        assert wanted in titles
    grace = next(h for h in library() if h.title == "Amazing Grace")
    assert grace.author == "John Newton"
    assert grace.stanzas[0].text.split("\n") == ["Amazing grace! How sweet the sound", "That saved a wretch like me!",
                                                  "I once was lost, but now am found;", "Was blind, but now I see."]


@pytest.mark.parametrize("query, title", [("abide with me", "Abide With Me"), ("how sweet the sound", "Amazing Grace"),
                                          ("WHAT A FRIEND", "What A Friend We Have In Jesus")])
def test_library_search_by_title_or_line(query, title):
    assert search_library(query)[0].title == title


def test_library_search_ignores_empty_or_unknown():
    assert search_library("") == [] and search_library("zzqx blorp") == []


BOOK = """1
Amazing Grace
Amazing grace! How sweet the sound
That saved a wretch like me!

2
'Twas grace that taught my heart to fear,
And grace my fears relieved;

3. Through many dangers, toils and snares
I have already come;

2. Abide With Me
1. Abide with me; fast falls the eventide;
The darkness deepens; Lord with me abide.

2. Swift to its close ebbs out life's little day;
Earth's joys grow dim

3
Holy Holy Holy
Holy, holy, holy! Lord God Almighty!
"""


def test_hymn_book_splits_on_hymn_numbers_not_verse_numbers():
    hymns = split_hymn_book(BOOK)
    assert [(n, t, len(s)) for n, t, s in hymns] == [("1", "Amazing Grace", 3), ("2", "Abide With Me", 2),
                                                     ("3", "Holy Holy Holy", 1)]
    assert hymns[0][2][0].text.startswith("Amazing grace!")


def test_yoruba_hymn_book_with_orin_numbers():
    text = "Orin 1\nE yin Oluwa\nE yin Oluwa, Oba ogo\n\nEgbe:\nHalleluyah\n\nOrin 2\nJesu fe mi\nJesu fe mi\n" \
           "\nOrin 3\nKristi Oba\nAwa yin O\n"
    hymns = split_hymn_book(text)
    assert [(n, t) for n, t, _ in hymns] == [("1", "E yin Oluwa"), ("2", "Jesu fe mi"), ("3", "Kristi Oba")]
    assert [s.label for s in hymns[0][2]] == ["Verse 1", "Chorus"]


def docx(path, paragraphs):
    body = "".join(f"<w:p><w:r><w:t>{escape(p)}</w:t></w:r></w:p>" for p in paragraphs)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", f'<w:document xmlns:w="w"><w:body>{body}</w:body></w:document>')
    return path


def test_word_hymn_book(tmp_path):
    path = docx(tmp_path / "Church hymns.docx", BOOK.split("\n"))
    assert read_docx(path).split("\n")[:3] == ["1", "Amazing Grace", "Amazing grace! How sweet the sound"]


PAGE = ("Great is Thy faithfulness, O God my Father,\n\nThere is no shadow of turning with Thee;\n\nThou changest not\n\n"
        "As Thou hast been Thou forever wilt be.\n\nRefrain:\n\nGreat is Thy faithfulness!\n\nMorning by morning\n\n"
        "All I have needed\n\nGreat is Thy faithfulness, Lord, unto me!\n\nSummer and winter\n\nSun, moon and stars\n\n"
        "Join with all nature\n\nTo Thy great faithfulness, mercy and love.\n\nRefrain\n\n")


def test_web_page_with_one_line_per_paragraph():
    stanzas = _stanzas_from(PAGE)
    assert [s.label for s in stanzas] == ["Verse 1", "Chorus", "Verse 2"]
    assert stanzas[1].text.startswith("Great is Thy faithfulness!")


def test_printed_hymnal_marks_are_removed():
    text = clean_lyrics("161—WHEN PEACE LIKE A RIVER\nIt is well . . . with my soul! . . .\nPraising (Last verse: Amen.)")
    assert text == "It is well with my soul!\nPraising"


def test_no_internet_is_an_error_the_window_can_show(monkeypatch):
    def offline(*_args, **_kwargs):
        raise OSError("no internet")
    monkeypatch.setattr(hymn_search.urllib.request, "urlopen", offline)
    with pytest.raises(OSError):
        hymn_search.search_online("amazing grace")


@needs_screen
def test_find_hymn_window_adds_a_free_hymn(make_app):
    app = make_app()
    app.show_page("media")
    app._pick_kind("hymn")
    app.find_hymn()
    app.hymn_find_online.set(False)
    app.hymn_find_query.insert(0, "abide with me")
    app.hymn_find_search()
    assert app.hymn_find_results.get(0).startswith("Abide With Me")
    app.hymn_find_add()
    [hymn] = app.media.hymns("")
    assert hymn.title == "Abide With Me" and len(hymn.stanzas) >= 5
    assert app.hymn_selected.id == hymn.id


@needs_screen
def test_import_word_hymn_book(make_app, tmp_path):
    app = make_app()
    added, problems = app.import_hymn_files([docx(tmp_path / "Our Hymns.docx", BOOK.split("\n"))])
    assert (added, problems) == (3, [])
    assert {h.collection for h in app.media.hymns("")} == {"Our Hymns"}
    assert [h.label for h in app.media.hymns("")] == ["1. Amazing Grace", "2. Abide With Me", "3. Holy Holy Holy"]
