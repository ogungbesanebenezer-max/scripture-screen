import pytest

from verse_screen.bible import Library, Ref
from verse_screen.detector import CHAPTER_WAIT, Detector
from verse_screen.references import ReferenceFinder

library = Library()


def make():
    return Detector(library, ReferenceFinder(library))


def twice(detector, text, now):
    """Live, every phrase is heard in at least two overlapping windows."""
    first, second = detector.process(text, now), detector.process(text, now + 1.5)
    second.found = first.found + second.found
    second.translation = first.translation or second.translation
    second.missing_translation = first.missing_translation or second.missing_translation
    return second


def test_overlapping_windows_report_once():
    detector = make()
    assert detector.process("our text is Romans 8:28", 0).found[0].ref == Ref("Romans", 8, 28)  # clear: at once
    assert detector.process("is Romans 8:28 and we know", 2).found == []
    assert detector.process("is Romans 8:28 and we know", 3.5).found == []
    assert detector.process("Romans 8:28 again", 40).found[0].ref == Ref("Romans", 8, 28)


def test_next_verse_counts_once_across_windows():
    detector = make()
    detector.process("John 3:16", 0)
    detector.process("John 3:16", 1.5)
    assert detector.process("next verse please", 30).found[0].ref == Ref("John", 3, 17)  # at once (owner)
    assert detector.process("next verse please", 31.5).found == []  # same words in the next window: not 18
    assert detector.process("next verse please", 33).found == []


def test_voice_switch_and_missing_translation():
    detector = make()
    assert detector.process("read that in the American Standard version.", 0).translation is None
    assert detector.process("read that in the American Standard version.", 1.5).translation == "ASV"
    assert detector.current == "ASV"
    detector.process("in the Amplified version", 3)
    assert detector.process("in the Amplified version", 4.5).missing_translation == "AMP"
    assert detector.current == "ASV"


def test_partial_window_does_not_replace_full_reference():
    detector = make()
    assert detector.process("Philippians 4:13", 0).found[0].ref == Ref("Philippians", 4, 13)
    assert detector.process("Philippians 4:13", 1.5).found == []
    assert detector.process("verse 7 Philippians 4", 3).found == []
    assert detector.process("verse 7 Philippians 4", 4.5).found == []


def test_misheard_bare_numbers_once_are_dropped():
    """Clear chapter-and-verse goes up at once (owner's choice); bare numbers heard once still wait."""
    detector = make()
    detector.process("Media, give me Proverbs 3 8", 0)
    assert detector.process("Philippians 4 13", 1.5).found == []
    assert detector.flush(10).found == []


def test_command_must_be_heard_twice():
    detector = make()
    assert detector.process("Media, project Jude 1 vs 7.", 0).found[0].kind == "command"  # clear: at once
    assert detector.process("Media, project Jude 1 vs 7.", 1.5).found == []


def test_single_misheard_command_never_appears():
    detector = make()
    assert detector.process("Media, give me Proverbs 3-8.", 0).found == []
    assert detector.process("Media, give me Proverbs 3 verse 5.", 1.5).found[0].ref == Ref("Proverbs", 3, 5)


def test_slow_laptop_does_not_block_detection():
    detector = make()
    assert detector.process("Romans 8:28", 6, seconds=5.0).found[0].ref == Ref("Romans", 8, 28)


def test_no_version_named_means_default():
    found = twice(make(), "Media, give me Genesis 1 vs 1.", 0).found[0]
    assert found.translation == "KJV" and found.triggered


def test_version_named_in_the_command_is_used_once():
    detector = make()
    first = twice(detector, "Media, give me Genesis 1 vs 1 in the American Standard version.", 0)
    assert first.found[0].translation == "ASV" and first.translation is None
    assert twice(detector, "Media, project Jude 1 vs 7.", 4).found[0].translation == "KJV"


def test_unavailable_version_warns_and_falls_back_to_default():
    events = twice(make(), "Media, give me John 3:16 in the Amplified version.", 0)
    assert events.missing_translation == "AMP" and events.found[0].translation == "KJV"


def test_follow_on_keeps_the_passage_translation():
    detector = make()
    twice(detector, "Media, give me John 3:16 in the American Standard version.", 0)
    assert twice(detector, "Media, verse 18.", 4).found[0].translation == "ASV"


@pytest.mark.parametrize("said", ["our text is Romans 8:28 today", "John 3 verse 16", "the Bible says in Romans 8:28",
                                  "remember what Paul wrote in Philippians 4 13", "Isaiah 53 5 tells us"])
def test_named_reference_shows_whatever_words_surround_it(said):
    detector = make()
    found = detector.process(said, 0).found + detector.process(said, 1.5).found
    assert len(found) == 1 and found[0].triggered is True


def test_numbers_for_something_else_stay_a_suggestion():
    detector = make()
    detector.process("we will spend Mark 5 minutes 3 people", 0)
    found = detector.process("we will spend Mark 5 minutes 3 people", 1.5).found
    assert not found or found[0].triggered is False


def run_sequence(detector, lines):
    """Each line is heard in two windows 1.5 s apart, as in a live service."""
    results, now = [], 0.0
    for line in lines:
        found = detector.process(line, now).found + detector.process(line, now + 1.5).found
        results.append(found[0] if found else None)
        now += 12
    return results


def test_pastor_moves_through_a_chapter():
    results = run_sequence(make(), ["Give me gen 1vs 1.", "Go to verse 8.", "Verse 2.", "Let go to 18."])
    assert [r.ref if r else None for r in results] == [
        Ref("Genesis", 1, 1), Ref("Genesis", 1, 8), Ref("Genesis", 1, 2), Ref("Genesis", 1, 18)]
    assert all(r.triggered for r in results)


@pytest.mark.parametrize("story", [
    "I lived in room 8 when I was 18 years old.", "I was 18 years old when I got saved.",
    "Go to 18 years of faithful service.", "We paid 8 naira for it.", "Look at the 8 people here.",
    "At 8 o'clock we will close.", "Turn to page 18.", "Go to room 8.", "He has 2 children.",
])
def test_stories_stay_off_the_screen(story):
    detector = make()
    twice(detector, "Media, give me Genesis 1 vs 1.", 0)
    detector.process(story, 15)
    assert detector.process(story, 16.5).found == []


def test_verse_that_does_not_exist_is_ignored():
    detector = make()
    twice(detector, "Media, give me Jude 1 vs 7.", 0)
    assert twice(detector, "Go to verse 99.", 15).found == []


def test_cut_off_command_waits_for_the_verse():
    detector = make()
    assert detector.process("Media, project Jude 1 verse", 0).found == []
    assert detector.process("Media, project Jude 1", 1.5).found == []
    assert twice(detector, "Media, project Jude 1, verse 7.", 3).found[0].ref == Ref("Jude", 1, 7)


def test_verse_mentioned_inside_a_sentence_goes_up():
    """Owner 2026-11-11: after Ephesians 6:1, the pastor's "verse 2" must move the screen, in any words."""
    detector = make()
    twice(detector, "Media, give me Genesis 1 vs 1.", 0)
    found = detector.process("in verse 2 he tells us about the deep", 15).found
    assert found[0].ref == Ref("Genesis", 1, 2) and found[0].triggered is True


def test_can_return_to_a_verse_quickly():
    detector = make()
    twice(detector, "Media, give me Genesis 1 vs 1.", 0)
    assert twice(detector, "Go to verse 8.", 10).found[0].ref == Ref("Genesis", 1, 8)
    assert twice(detector, "Go to verse 2.", 20).found[0].ref == Ref("Genesis", 1, 2)
    assert twice(detector, "Go to verse 8.", 30).found[0].ref == Ref("Genesis", 1, 8)


def test_version_heard_just_after_the_reference_still_applies():
    detector = make()
    assert twice(detector, "Media, project Jude 1 verse 7.", 0).found[0].translation == "KJV"
    assert detector.process("Media, project Jude 1 verse 7 in the American Standard version.", 3).translation == "ASV"


def test_stand_alone_verse_without_full_stop_runs_into_next_command():
    detector = make()
    twice(detector, "Media, give me Genesis 1 vs 1.", 0)
    found = twice(detector, "Go to verse 8 Verse 2, let's go to 18.", 15).found
    assert [f.ref.verse for f in found] == [8, 2, 18] and all(f.triggered for f in found)


def test_clear_verse_heard_once_goes_up_after_a_quiet_pass():
    """Real log 2026-11-11: 'Proverb 11 verse 8.' heard in one pass only, then 13 s of nothing; it was lost."""
    detector = make()
    found = detector.process("Proverb 11 verse 8.", 100, seconds=2.7).found
    assert [f.ref for f in found] == [Ref("Proverbs", 11, 8)] and found[0].triggered  # owner: clear = at once
    assert detector.flush(110).found == []


def test_clear_verse_heard_once_goes_up_when_the_next_pass_has_no_verse():
    detector = make()
    assert detector.process("Media, give me Proverbs 11 8.", 0, seconds=2.7).found == []
    assert [f.ref for f in detector.process("Can we turn? One more.", 3, seconds=2.7).found] == [Ref("Proverbs", 11, 8)]


def test_held_verse_is_dropped_when_the_next_pass_hears_another():
    detector = make()
    detector.process("Media, give me 2 Corinthians 1 7", 0)
    found = detector.process("Philippians 4:13", 3).found
    assert all(f.ref != Ref("2 Corinthians", 1, 7) for f in found + detector.flush(20).found)


def test_held_verse_is_dropped_when_taken_back():
    detector = make()
    detector.process("Media, give me John 3 16.", 0)
    assert detector.process("no wait, let's make it", 3).found == []
    assert detector.flush(10).found == []


def test_bare_numbers_heard_once_still_wait_for_a_second_hearing():
    detector = make()
    detector.process("Media, give me Proverbs 3-8.", 0)
    assert detector.flush(10).found == []


def test_real_service_log_2026_11_11():
    """Lines exactly as heard on the owner's laptop through the phone mic."""
    detector, shown, now = make(), [], 0.0
    for line in ["Ephesians 6 verse 1.", "Ephesians 6 verse 1.", "Vastu!", "Do you have anything to add?",
                 "Ephesians 6, verse 3,", "Verse 4.", "First John chapter 1.", "Prost. John chapter 1.",
                 "Thank you Lord John. First John chapter 1.", "4th John chapter 1 verse 1.", "verse 9"]:
        shown += [f.ref.label for f in detector.process(line, now, seconds=2.7).found if f.triggered]
        now += 3
    assert shown == ["Ephesians 6:1", "Ephesians 6:2", "Ephesians 6:3", "Ephesians 6:4", "1 John 1:1", "1 John 1:9"]


@pytest.mark.parametrize("said,label", [
    ("4th John chapter 1.", "1 John 1:1"), ("Prost. John chapter 2 verse 3", "1 John 2:3"),
    ("fourth Corinthians 13 verse 4", "1 Corinthians 13:4"), ("forth Peter chapter 5 verse 7", "1 Peter 5:7"),
    ("Second Chronicles chapter 7 verse 14", "2 Chronicles 7:14"), ("First Samuel chapter 17", "1 Samuel 17:1"),
    ("3rd John verse 2", "3 John 1:2"), ("John chapter 3 verse 16", "John 3:16"),
])
def test_numbered_books(said, label):
    detector = make()
    found = detector.process(said, 0).found or detector.flush(CHAPTER_WAIT).found  # a chapter alone waits for its verse
    assert [f.ref.label for f in found] == [label] and found[0].triggered


def test_range_then_verse_by_verse_stays_in_the_passage():
    """Owner 2026-11-11: 'Matthew 2 vs 17 to 22', then next verse / verse 18 / 19: stay there, never another book."""
    detector, shown, now = make(), [], 0.0
    for line in ["Matthew 2 verse 17 to 22.", "Matthew chapter 2 verse 17 to 22", "next verse", "Verse 19",
                 "verse 20 says", "Let's read verse 21", "Vastu!", "next verse please"]:
        shown += [f.ref.label for f in detector.process(line, now, seconds=2.7).found if f.triggered]
        now += 10
    assert shown == ["Matthew 2:17-22", "Matthew 2:18", "Matthew 2:19", "Matthew 2:20", "Matthew 2:21", "Matthew 2:22"]


def test_far_jump_needs_a_second_hearing():
    detector = make()
    detector.process("Matthew 2 verse 21", 0)
    assert detector.process("verse 2", 10).found == []  # heard once, far from 21: could be a mishearing
    assert detector.process("verse 2", 12).found[0].ref == Ref("Matthew", 2, 2)  # said again: go


def test_next_verse_never_runs_into_another_book():
    detector = make()
    detector.process("Jude verse 25", 0)
    assert detector.process("next verse", 10).found == []


def test_real_service_log_2026_11_11_second_run():
    """Exact lines from the owner's laptop, 12:04-12:06: verse 3 and 5 came through as 'Vastry!' / 'That's three.'"""
    detector, shown, now = make(), [], 0.0
    for line in ["Matthew 6, verse 1,", "Matthew 6, verse 1.", "Okay.", "Okay. Verse 2.", "Okay, verse 2.",
                 "That's three.", "Vastry!", "Vastry!", "Verse 4.", "It's fall.", "That's five.", "Yes.",
                 "Pause to join chapter 1, verse 1.", "Join me now, Lord.", "Second, join chapter 1 verse 2."]:
        shown += [f.ref.label for f in detector.process(line, now, seconds=2.8).found if f.triggered]
        now += 3
    assert shown == ["Matthew 6:1", "Matthew 6:2", "Matthew 6:3", "Matthew 6:4", "Matthew 6:5", "1 John 1:1",
                     "2 John 1:2"]


def test_thats_a_number_without_a_passage_is_nothing():
    assert make().process("That's three.", 0).found == []


@pytest.mark.parametrize("said,label", [
    ("Matthew, Genesis, 6, verse 1.", "Matthew 6:1"),  # owner 2026-11-11: asked for Matthew, screen showed Genesis
    ("Romans, Genesis 8 verse 28", "Romans 8:28"),
    ("Genesis 6 verse 1", "Genesis 6:1"),
    ("In Matthew and Genesis 6 verse 1", "Genesis 6:1"),
])
def test_genesis_heard_in_place_of_chapter(said, label):
    assert [f.ref.label for f in make().process(said, 0).found] == [label]


def test_suggestion_then_clear_hearing_goes_up():
    """12:02 log: 'Philemon, Philemon, chapter one.' was only suggested, and the clear one after it was blocked."""
    detector = make()
    first = detector.process("Philemon, Philemon, chapter one.", 0, seconds=4.6).found
    assert first and first[0].triggered is False
    found = detector.process("Heavily flu, philemon, philemon, chapter 1 verse 1.", 3, seconds=4.6).found
    assert found[0].ref == Ref("Philemon", 1, 1) and found[0].triggered


@pytest.mark.parametrize("lines", [
    ["Exodus.", "chapter 14 verse 14"],
    ["Exodus. Chapter 14 verse 14."],
    ["Turn to the book of Exodus.", "chapter 14, verse 14."],
    ["Exodus.", "Chapter 14.", "verse 14"],
])
def test_book_said_alone_then_chapter_and_verse(lines):
    """12:25 log: 'Exodus.' was heard on its own and nothing went up."""
    detector, shown = make(), []
    for i, line in enumerate(lines):
        shown += [f.ref.label for f in detector.process(line, i * 4, seconds=4.6).found if f.triggered]
    assert shown[-1] == "Exodus 14:14"


def test_book_word_in_normal_talk_does_not_steal_the_chapter():
    detector = make()
    twice(detector, "Media, give me Romans 8 verse 1.", 0)
    detector.process("Mark my words.", 20, seconds=4.6)
    found = detector.process("chapter 8 verse 28", 24, seconds=4.6).found
    assert all(f.ref.book == "Romans" for f in found)


@pytest.mark.parametrize("said,label", [
    ("Matthew, Shaptah, 2, verse 1.", "Matthew 2:1"),  # 12:46 log
    ("Matthew, one of us one.", "Matthew 1:1"),  # 12:46 log
    ("Romans chapta 8 verse 28", "Romans 8:28"),
    ("Psalm, Shapter, 23, verse 1", "Psalm 23:1"),
    ("Matthew, Mark 2 verse 1", "Mark 2:1"),
])
def test_accent_forms_of_chapter_and_vs(said, label):
    assert [f.ref.label for f in make().process(said, 0, seconds=4.6).found] == [label]


# ---- sermon tests 2026-11-21 (Enenche, Ibiyeomie, Adeboye, Okonkwo): lines exactly as the app heard them ----
def _screen(lines, step=1.6):
    """What would go on screen, with the app's flush between passes."""
    detector, shown, now = make(), [], 0.0
    for line in lines:
        for tick in range(1, int(step / 0.4)):
            shown += [f.ref.label for f in detector.flush(now + tick * 0.4).found if f.triggered]
        now += step
        shown += [f.ref.label for f in detector.process(line, now, seconds=1.6).found if f.triggered]
    for tick in range(1, 20):
        shown += [f.ref.label for f in detector.flush(now + tick * 0.4).found if f.triggered]
    return shown


def test_chapter_then_verse_shows_only_the_verse():
    assert _screen(["Safety is of the Lord.", "Proverbs, chapter 21.", "Proverbs 21, verse 13.",
                    "Proverbs, chapter 21, verse 13."]) == ["Proverbs 21:13"]
    assert _screen(["Daniel, Daniel, chapter 5.", "Daniel, Daniel chapter 5, verse 11.",
                    "Daniel chapter 5, verse 11, verse 12. There is a man!"])[0] == "Daniel 5:11"


def test_chapter_alone_still_goes_up_when_no_verse_follows():
    assert _screen(["Turn with me to Hebrews chapter 12.", "Let us look at what the writer is saying here.",
                    "He is talking about running the race.", "And looking unto Jesus."]) == ["Hebrews 12:1"]


def test_from_about_22_to_27():
    assert _screen(["One of the greatest losers in history. In 2nd Kings, chapter 5.",
                    "In 2 Kings 5, from about 22 to 27, 2 Kings 5,", "In 2 Kings 5, 27, 2 Kings 5, 27.",
                    "From verse 22 to 27, 2 Kings 5 from 22 to 27."]) == ["2 Kings 5:22-27"]


def test_a_dropped_digit_does_not_replace_the_verse():
    assert _screen(["The Master Jesus knew himself in John chapter 18 verse 37.",
                    "The Master Jesus knew himself in John chapter 18 verse 37.",
                    "Jesus knew Himself in John chapter 8, verse 37, when Pilate spoke.",
                    "In John chapter 18 verse 37, when Pilate spoke and said, Are you the king?"]) == ["John 18:37"]


def test_summary_is_samuel_not_the_last_book():
    assert _screen(["Proverbs 21, verse 13.", "Proverbs, chapter 21, verse 13.", "You know the secrets of victory.",
                    "For summary chapter 2 verse 9.", "For summary chapter 2 verse 9."]) == ["Proverbs 21:13",
                                                                                           "1 Samuel 2:9"]


def test_a_run_of_numbers_is_not_a_reference():
    assert _screen(["2 Corinthians 5, 7, 5, 5, 2, 1, 1, 1, 2, 2, 2, 1,", "And he knew no sin."]) == []


def test_chapter_said_twice_is_not_the_verse():
    assert _screen(["In Judges 15, chapter 15, verse 9 to 13.", "In Judges 15, chapter 15, verse 9 to 13."]) == [
        "Judges 15:9-13"]


def test_first_psalm_is_samuel():
    """Parakeet heard '1 Samuel chapter 2 verse 9' as 'First Psalm chapter 2' and '1 Psalm 8 chapter 2 verse 9'."""
    assert _screen(["Constant victory. First Psalm chapter 2, verse 9.", "1 Samuel chapter 2, verse 9.",
                    "1 Psalm 8 chapter 2 verse 9. 1 Psalm 8 chapter 2 verse 9."]) == ["1 Samuel 2:9"]
    assert _screen(["Psalm 23 verse 1.", "Psalm 23 verse 1."]) == ["Psalm 23:1"]


def test_read_chapter_waits_for_its_verse_too():
    assert _screen(["When you read Hebrews chapter 1,", "When you read Hebrews chapter 12",
                    "When you read Hebrews chapter 12, verse 15 to 16,"]) == ["Hebrews 12:15-16"]
