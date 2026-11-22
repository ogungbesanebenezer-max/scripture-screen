"""Mishearings seen when Nigerian English voices were run through the speech model (scripts/accent_bench.py)."""
import pytest

from verse_screen.bible import Library
from verse_screen.references import ReferenceFinder

FINDER = ReferenceFinder(Library())


def first(text):
    found = FINDER.find(text)
    return found[0].ref.label if found else None


@pytest.mark.parametrize("heard, expected", [
    ("Let us open our Bibles to John chapter 3 verse 60.", "John 3:16"),  # thirteen-sixteen heard as thirty-sixty
    ("Philippians, chapter 4, verse 30, I can do all things.", "Philippians 4:13"),
    ("Hebrews chapter 30 verse 8", "Hebrews 13:8"),
    ("Look at Deuteronomy, chapter 28 verse 30.", "Deuteronomy 28:30"),  # a real verse 30 is left alone
    ("John 3 verse 30", "John 3:30"),
    ("Media, Project U chapter 1 verse 7.", "Jude 1:7"),
    ("Media, project you chapter 1 verse 7.", "Jude 1:7"),
    ("Galatians 5x22, the foot of the Spirit.", "Galatians 5:22"),
    ("The chorach chapter 4 verse 6, not by might.", "Zechariah 4:6"),
    ("Media, give me second Corinthians 5.70.", "2 Corinthians 5:17"),
    ("The Bible says in Prolabs chapter 18, verse 21.", "Proverbs 18:21"),
])
def test_accent_mishearings(heard, expected):
    assert first(heard) == expected


@pytest.mark.parametrize("speech", ["Media give you 2 minutes", "I am telling you 3 things", "thank you 2 times"])
def test_everyday_you_is_not_jude(speech):
    assert first(speech) is None
