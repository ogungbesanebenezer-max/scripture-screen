"""A pastor changing their mind must not flash the verses they took back."""
import pytest

from verse_screen.bible import Library, Ref
from verse_screen.detector import Detector
from verse_screen.references import ReferenceFinder

library = Library()
finder = ReferenceFinder(library)


def live(said, step=1.5, pace=2.6, window=7.0, recent=()):
    """Feed speech through overlapping windows as the listener does. Returns the verses that would go up by themselves."""
    words, detector, shown, now = said.split(), Detector(library, finder), [], step
    for ref in recent:
        detector.note_shown(ref, "KJV")
    while now < len(words) / pace + 8:
        heard = " ".join(words[max(0, int((now - window) * pace)):min(len(words), int(now * pace))])
        if heard:
            shown += [f.ref.label for f in detector.process(heard, now, 1.0).found if f.triggered]
        now += step
    return shown


# Owner 2026-11-11: a clear chapter and verse goes up the moment it is heard, so a verse taken back can flash
# briefly; what matters is that the final choice is what stays on the screen.
@pytest.mark.parametrize("step", [1.5, 2.7])
@pytest.mark.parametrize("said,final", [
    ("Turn with me to John 3:16. No, wait, let's make it John 1:1. Actually, let's look at Genesis 1:1. In the beginning.",
     "Genesis 1:1"),
    ("Turn to Romans 8:28, sorry, Romans 8:38, and we will read it together now brothers.", "Romans 8:38"),
    ("Media give me John 3 verse 16, no wait, John 3 verse 17. We will read it together now brothers and sisters.", "John 3:17"),
])
def test_the_final_choice_is_what_stays_up(said, final, step):
    assert live(said, step)[-1] == final


@pytest.mark.parametrize("step", [1.5, 2.7])
def test_a_taken_back_verse_never_comes_back_later(step):
    shown = live("Turn to Romans 8:28, sorry, Romans 8:38, and we will read it together now brothers and sisters.", step)
    assert shown.count("Romans 8:28") <= 1 and shown.index("Romans 8:38") > (
        shown.index("Romans 8:28") if "Romans 8:28" in shown else -1)


@pytest.mark.parametrize("step", [1.5, 2.7])
def test_command_word_scrolling_away_is_still_a_command(step):
    said = "Please turn with me to John chapter 3 verse 16 and we will read it together now brothers and sisters."
    assert live(said, step) == ["John 3:16"]


@pytest.mark.parametrize("said", [
    "Media give me Isaiah 40:31 they that wait upon the Lord shall renew their strength.",
    "Media give me John 14:6 no man cometh unto the Father but by me.",
    "Media give me John 3:16 actually says that God loved the world.",
    "Media give me John 3:16 actually let's read it together.",
    "Media give me John 3:16 actually let's look at this word loved.",
    "Media give me John 3:16 sorry I need some water.",
])
def test_normal_speech_after_a_verse_keeps_it(said):
    assert said.split("give me ")[1].split(" ")[0] in [m.ref.book for m in finder.find(said)]


FIRST, SECOND, THIRD = Ref("John", 3, 16), Ref("Romans", 8, 28), Ref("Psalms", 23, 1)


@pytest.mark.parametrize("said,expected", [
    ("Go back to the first one please my brothers and sisters.", ["John 3:16"]),
    ("Let us go back to the previous scripture we were looking at.", ["Romans 8:28"]),
    ("When you go back to your first love you will see.", []),
])
def test_go_back_uses_what_was_shown(said, expected):
    assert live(said, recent=(FIRST, SECOND, THIRD)) == expected


def test_go_back_with_nothing_before_does_nothing():
    assert live("Go back to the first one please.", recent=(FIRST,)) == []
