"""A misheard book name must never put a verse from the old passage on screen."""
import pytest

from verse_screen.bible import Library, Ref
from verse_screen.references import ReferenceFinder

library = Library()
finder = ReferenceFinder(library)
GENESIS = Ref("Genesis", 1, 1)


def labels(said):
    return [match.ref.label for match in finder.find(said, context=GENESIS)]


@pytest.mark.parametrize("said", [
    "Media, give me Prophets 18 verse 11.",
    "Media, give me Probably 18 vs 11.",
    "Media, give me Prophets chapter 18 verse 11.",
    "18 verse 11.",  # the listening window cut off the book
])
def test_unread_book_does_not_divert_to_the_old_passage(said):
    assert labels(said) == []


@pytest.mark.parametrize("word", ["vs", "vs.", "V.S.", "V S", "verse", "was", "first", "fast", "vest", "bus", "ver", "as"])
def test_any_word_for_verse_after_media(word):
    assert labels(f"Media, give me Proverbs 18 {word} 1.") == ["Proverbs 18:1"]
    assert labels(f"Media, give me Proverb 18 {word} 11.") == ["Proverbs 18:11"]


@pytest.mark.parametrize("said,expected", [
    ("Give me Mark 5 minutes 3.", []),  # 5 minutes is not a verse
    ("Media, give me John 3 and 4.", ["John 3:1"]),  # two chapters, not chapter 3 verse 4
    ("Media, give me Proverbs 18 was 99.", []),  # no such verse: never guess
    ("Media, give me Proverbs 18. 11 people came.", ["Proverbs 18:1"]),
])
def test_loose_verse_word_stays_careful(said, expected):
    assert labels(said) == expected


def test_without_media_a_strange_word_is_not_a_verse():
    assert [m.ref.label for m in finder.find("Proverbs 18 was 11.")] != ["Proverbs 18:11"]


@pytest.mark.parametrize("said,expected", [
    ("Go to verse 8.", ["Genesis 1:8"]),
    ("Verse 2.", ["Genesis 1:2"]),
    ("Media, give me verse 8.", ["Genesis 1:8"]),
    ("Go back to verse 8.", ["Genesis 1:8"]),
    ("Go to chapter 5 verse 3.", ["Genesis 5:3"]),
    ("Media, give me Genesis 1 verse 1. Go to verse 8.", ["Genesis 1:1", "Genesis 1:8"]),
    ("Media, give me Proverbs 18 vs 11.", ["Proverbs 18:11"]),
])
def test_real_moves_still_work(said, expected):
    assert labels(said) == expected
