"""First real service, 2026-11-22: what the laptop heard for the three verses it got wrong, replayed line by line."""
import pytest

from verse_screen.bible import Library, Ref
from verse_screen.detector import Detector
from verse_screen.references import ReferenceFinder
from verse_screen.semantic import SemanticIndex

library = Library()
finder = ReferenceFinder(library)


@pytest.fixture(scope="module")
def semantic():
    return SemanticIndex(library)


def labels(said):
    return [match.ref.label for match in finder.find(said)]


@pytest.mark.parametrize("said, verse", [
    ("See, like that man, Datman chapter 6 verse 3, be careful if you can", "Daniel 6:3"),
    ("Daniel chapter sees one to be a technique. Daniel distinguished himself so much", "Daniel 6:1"),
    ("Can you give us that please? That Joseph chapter 24 verse 15. We want to pray prayer.", "Joshua 24:15"),
    ("give us that please that closure chapter 24 verse 15 we want to pray prayer", "Joshua 24:15"),
])
def test_book_names_heard_wrong_that_day(said, verse):
    assert labels(said) == [verse]


@pytest.mark.parametrize("said", [
    "I said darling, sit down. My dinner was good.",
    "Joseph chapter 41 verse 37",  # Joshua has 24 chapters: Joseph's story is Genesis, so nothing
    "Tell your brother joseph that I am going",
    "Closure is what the family needed.",
])
def test_ordinary_talk_is_left_alone(said):
    assert labels(said) == []


def run(detector, lines, start=0.0, step=1.6):
    """Feed heard lines a pass apart, with the app's flushes between them; return what went up and what was offered."""
    up, offered, now = [], [], start
    for line in lines:
        for found in detector.flush(now - 0.1).found + detector.process(line, now, 1.5).found:
            (up if found.triggered else offered).append((found.ref.label, found.kind))
        now += step
    for _ in range(6):
        now += step
        for found in detector.flush(now).found:
            (up if found.triggered else offered).append((found.ref.label, found.kind))
    return up, offered


def test_misheard_chapter_does_not_replace_the_verse_being_read(semantic):
    detector = Detector(library, finder, semantic=semantic)
    up, _ = run(detector, [
        "Jesus teaches us in Matthew chapter 6 verse 33",
        "teaches us in matthew chapter 6 verse 33 matthew chapter 6 verse 3",
        "us in Matthew chapter 6 verse 33 Matthew chapter 6 verse 33 to seek first became",
        "6 verse 33. Matthew chapter 6 verse 33 said, To seek first the kingdom of God.",
        "Matthew chapter 7, to seek first the kingdom of God, to make him our priority.",
        "He said, for seek faith the kingdom of God in our prayers.",
        "King of God in our prayer.",
        "Make him our priority.",
    ])
    assert [label for label, _ in up] == ["Matthew 6:33"]


def test_a_new_chapter_still_goes_up_when_the_words_are_new(semantic):
    detector = Detector(library, finder, semantic=semantic)
    up, _ = run(detector, [
        "Jesus teaches us in Matthew chapter 6 verse 33",
        "Matthew chapter 6 verse 33 said, To seek first the kingdom of God.",
        "Now turn with me to Matthew chapter 7.",
        "We are going to look at judging others.",
        "Let us read it together.",
    ])
    assert [label for label, _ in up] == ["Matthew 6:33", "Matthew 7:1"]


def test_numbers_without_a_book_are_offered_by_their_words(semantic):
    detector = Detector(library, finder, semantic=semantic)
    up, offered = run(detector, [
        "That does chapter 24, verse 15. We want to pray. A family that sat.",
        "And as for me and my household, we will serve the Lord.",
        "choose you this day whom you will serve",
    ])
    assert up == []
    assert ("Joshua 24:15", "book_guess") in offered


def test_a_suggestion_does_not_block_the_reference_said_clearly(semantic):
    detector = Detector(library, finder, semantic=semantic)
    up, offered = run(detector, [
        "And my household, as for me, I will serve the Lord. Are you ready?",
        "As for me and my house, we will serve the Lord.",
        "Can you give us that please? That Joseph chapter 24 verse 15. We want to pray prayer.",
        "give us that please that Joshua chapter 24 verse 15 we want to pray prayer",
    ])
    assert "Joshua 24:15" in [label for label, _ in up]
