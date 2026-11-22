import pytest

from verse_screen.bible import Library, Ref
from verse_screen.references import ReferenceFinder
from verse_screen.voice import requested_translation

library = Library()
finder = ReferenceFinder(library)


@pytest.mark.parametrize("said,label", [
    ("Turn with me to John chapter three verse sixteen", "John 3:16"),
    ("John 3:16", "John 3:16"), ("John 316", "John 3:16"), ("john three sixteen", "John 3:16"),
    ("Proverbs 2, 2", "Proverbs 2:2"), ("Second Timothy 1:7", "2 Timothy 1:7"),
    ("first corinthians thirteen verse four to seven", "1 Corinthians 13:4-7"),
    ("Psalm 119 verse 105", "Psalm 119:105"), ("psalm one hundred and nineteen verse one hundred and five", "Psalm 119:105"),
    ("Psalm one nineteen verse one oh five", None), ("psalm twenty three", "Psalm 23:1"),
    ("provabs tree five", "Proverbs 3:5"), ("happen cock two four", "Habakkuk 2:4"),
    ("Jude 5", "Jude 1:5"), ("Jude chapter 1 verse 5", "Jude 1:5"), ("Song of Solomon 2:1", "Song of Solomon 2:1"),
    ("acts of the apostles 2 38", "Acts 2:38"), ("Revelations 3 20", "Revelation 3:20"),
    ("romance 8 28", "Romans 8:28"), ("Deutoronomy 28 1", "Deuteronomy 28:1"),
    ("give me a second John 3:16", "John 3:16"), ("Philippians 4-13", "Philippians 4:13"),
    ("Media give me Genesis 1 vs 1", "Genesis 1:1"), ("media project jude 1 vs 7", "Jude 1:7"),
    ("Genesis one versus one", "Genesis 1:1"), ("Romans 12 vs. 2", "Romans 12:2"),
    ("John 3 chapter 16", "John 3:16"),
    ("media give me June 1 vs 7", "Jude 1:7"), ("media project Judas 1 verse 7", "Jude 1:7"),
    ("media give me Jenises 1 vs 1", "Genesis 1:1"), ("media give me then it says 1 vs 1", "Genesis 1:1"),
    ("on June 1 we travelled", None), ("then it says 2 things", None), ("media give me Jude 2 vs 7", None),
    ("give me 5 minutes", None), ("show me mercy 3 times", None),
    ("John 3:99", None), ("Genesis 51:1", None),
    ("John went home with 3 friends", None), ("Mark 5 people came", None),
    ("the number 3 2 kings", None), ("I have 5 loaves and 2 fish", None),
    ("he is a judge 3 times over", None), ("market 5 stalls", None),
])
def test_spoken_references(said, label):
    found = finder.find(said)
    assert (found[0].ref.label if found else None) == label


def test_plain_book_names_are_not_marked_as_corrected():
    assert finder.find("the book of Romans chapter 8 verse 28")[0].kind == "exact"


def test_every_book_is_recognised():
    for book in library.shape:
        name = "Psalm" if book == "Psalms" else book
        assert finder.find(f"{name} 1:1")[0].ref == Ref(book, 1, 1)


@pytest.mark.parametrize("said,is_command", [
    ("Media, give me Genesis 1 vs 1", True), ("media project jude 1 vs 7", True),
    ("Media, Romans 8:28", True), ("Media team, please bring up the book of Romans 8:28", True),
    ("Project Psalm 23 verse 4", True), ("Can I have John 3:16 on the screen", True),
    ("Open with me to Romans 8:1", True), ("Look at John 3:16", True), ("Display Genesis 1 1", True),
    ("Let's read Romans 8:1", True), ("I have John 3:16 written down", False),
    ("as it is written in John 3:16", False), ("I thank the media team. Our text is John 3:16", False),
])
def test_commands_to_media(said, is_command):
    assert (finder.find(said)[0].kind == "command") == is_command


@pytest.mark.parametrize("said,score", [
    ("Media give me Gen 1 vs 1", 130), ("Genesis 1 verse 1", 100), ("give me Genesis 1 1", 110), ("Genesis 1 1", 80),
])
def test_confidence(said, score):
    assert finder.find(said)[0].confidence == score


def test_follow_up_uses_context():
    assert finder.find("look at verse 18", context=Ref("John", 3, 16))[0].ref == Ref("John", 3, 18)
    assert finder.find("look at verse 18")== []


def test_typed_abbreviations():
    assert finder.find("1 cor 13 4", typed=True)[0].ref == Ref("1 Corinthians", 13, 4)
    assert finder.find("mark 5", typed=True)[0].ref == Ref("Mark", 5, 1)
    assert finder.find("esth 8 9", typed=True)[0].ref == Ref("Esther", 8, 9)
    assert finder.find("2 thes 1 3", typed=True)[0].ref == Ref("2 Thessalonians", 1, 3)


@pytest.mark.parametrize("said,code", [
    ("Let's look at that in the Amplified version", "AMP"), ("Give me King James", "KJV"),
    ("read it in the New King James", "NKJV"), ("the King James was written in 1611", None),
    ("let us read American Standard", "ASV"), ("amplify your voice", None),
])
def test_voice_translation(said, code):
    assert requested_translation(said) == code


def test_translations_complete():
    assert library.codes[0] == "KJV"
    assert len(library.shape) == 66
    assert "only begotten" in library.text("KJV", Ref("John", 3, 16))
    assert library.step(Ref("Malachi", 4, 6), 1) == Ref("Matthew", 1, 1)
    assert library.step(Ref("Matthew", 1, 1), -1) == Ref("Malachi", 4, 6)
