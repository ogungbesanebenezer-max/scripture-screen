"""Turn each transcript window into events. No audio or UI here, so it is fully testable."""
import re
import threading
from dataclasses import dataclass, field, replace

from .bible import Ref
from .references import AUTO_THRESHOLD, SCORE_COMMAND

# A slow laptop often hears a short phrase in only one pass, and waiting for a second hearing dropped a clear
# "Proverbs 11 verse 8" entirely. So a clear reference heard once is held for one more pass: if that pass hears no
# other verse it goes up; if it hears a different verse (one misheard pass) it is dropped.
SINGLE_HEARING_SCORE = 80  # a book with its numbers, not mixed into conversation
CHANGE_OF_MIND = re.compile(r"\b(no|nope|wait|actually|sorry|rather|instead|correction|pardon|i mean|make it|scratch that"
                            r"|my mistake|second thought|not that one|change that)\b", re.IGNORECASE)
NUMBER_CUT_SECONDS = 20.0
BOOK_NAMED_SECONDS = 20.0
NEAR_VERSES = 10  # a follow-on this close to the verse in play (or inside the passage read out) moves at once
PENDING_WAIT = 4.0  # about one listening pass on a slow laptop; quiet this long = nothing contradicted it
PENDING_KINDS = {"exact", "fuzzy", "phonetic", "command"}
# Sermon tests 2026-11-21: "Proverbs, chapter 21." put up Proverbs 21:1 and "verse 13" came a second later; 12 such
# flashes in 80 minutes. A chapter said alone now waits this long for its verse number before verse 1 goes up.
CHAPTER_WAIT = 3.5
CHAPTER_WAIT_MOST = 10.0  # while verse numbers of that chapter are being heard (not yet confirmed), wait up to this
# "John chapter 18 verse 37" went up, then a pass that dropped a digit ("chapter 8, verse 37") replaced it.
DROPPED_DIGIT_SECONDS = 12.0
# A chapter said alone is not shown while the words heard with it are clearly the verse already on screen.
STILL_READING_SECONDS = 30.0
STILL_READING_SCORE = 0.5
STILL_READING_MARGIN = 0.1
from .voice import requested_translation

CUE = re.compile(r"\b(bible says|scripture says|scriptures say|it is written|that verse|the verse|the word says|"
                 r"word of god says|jesus said|paul said|david said|the lord said|god said|as it says)\b", re.I)
LEAD_IN = re.compile(r"^.*?\b(that verse (?:where|that) (?:it )?says,?|the verse (?:where|that) (?:it )?says,?|"
                     r"the bible says,?(?: that)?|scripture says,?(?: that)?|it is written,?(?: that)?|"
                     r"the word says,?(?: that)?)\s+", re.I)
# Earlier verses by position, not by content. "Previous verse" is left out: it means the verse above.
GO_BACK = re.compile(r"\b(?:go|come|turn|take us|bring us)\s+back\s+to\s+(?:the\s+)?(first|previous|last|earlier|other)\s+"
                     r"(?:one|scripture|passage|reference)\b", re.I)
RECENT_VERSES = 3
# "That does chapter 24, verse 15": the numbers came through but the book did not (Joshua, service 2026-11-22).
# The words around them (quoted before or after) pick the book among verses with those numbers; a suggestion only.
BOOKLESS = re.compile(r"\bchapter\s+(\d{1,3})[,.]?\s+(?:(?:verse|verses|vs)\s+)?(\d{1,3})\b", re.I)
BOOK_GUESS_SECONDS = 20.0  # words heard this long before and after the numbers count
BOOK_GUESS_SCORE = 0.55
BOOK_GUESS_MARGIN = 0.04


@dataclass
class Found:
    ref: object
    kind: str
    score: float | None = None
    translation: str | None = None
    triggered: bool = False  # confident enough to change the preview card (and go live in Auto mode)


@dataclass
class Events:
    found: list = field(default_factory=list)
    translation: str | None = None  # voice switch of the current passage
    missing_translation: str | None = None


def sentences_of(transcript):
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", transcript) if part.strip()]


class Detector:
    """Windows overlap, so the same words arrive several times; cooldowns stop repeats."""

    def __init__(self, library, finder, semantic=None, default="KJV", cooldown=25.0, next_gap=8.0,
                 threshold=0.62, cued_threshold=0.55, min_words=6, confirm_gap=8.0, voice_cooldown=10.0,
                 command_cooldown=9.0, context_life=900.0):
        self.library = library
        self.finder = finder
        self.semantic = semantic
        self.default = default if default in library.codes else library.codes[0]
        self.current = self.default  # translation of the passage in play
        self.cooldown = cooldown
        self.next_gap = next_gap
        self.threshold = threshold
        self.cued_threshold = cued_threshold
        self.min_words = min_words
        self.confirm_gap = confirm_gap
        self.voice_cooldown = voice_cooldown
        self.window_limit = 3.5
        self.can_confirm = True
        self.heard = {}  # item -> time last heard, for two-window confirmation
        self.command_cooldown = command_cooldown  # just over the 7 s window, so a verse can be revisited quickly
        self.context_life = context_life
        self.context_time = None
        self.last_now = None
        self.seen = {}
        self.seen_chapters = {}
        self.context = None
        self.last_next = -1e9
        self.last_voice = {}
        self.commands = {}  # ref -> (time, match) for references the pastor directed at the media team
        self.recent = []  # (ref, translation) of the last verses shown, oldest first
        self.last_reported = None  # (ref, time) of the newest verse reported
        self.book_named = None  # (book, time): "Exodus." said alone; "chapter 14 verse 14" may follow it
        self.suggested = set()  # refs listed only as a suggestion; a clear hearing may still put them up
        self.passage = None  # the range read out, like Matthew 2:17-22; verse-by-verse moves inside it are expected
        self.pending = None  # (match, time): a clear reference heard once, waiting one more pass
        self.chapter_hold = None  # (match, time, first time): "Proverbs chapter 21." waiting for its verse
        self.hold_words = []  # what was heard while the chapter waited, to check it against the verse on screen
        self.recent_words = []  # (time, transcript) of the last passes, for a book lost in the hearing
        self.bookless = None  # (chapter, verse, time): numbers heard with no book, waiting for the quoted words
        self.lock = threading.Lock()  # process runs on the listening thread, flush on the screen thread

    def note_shown(self, ref, code):
        if not self.recent or self.recent[-1][0] != ref:
            self.recent = (self.recent + [(ref, code)])[-RECENT_VERSES:]

    def set_context(self, ref):
        self.context, self.context_time = ref, self.last_now

    def flush(self, now):
        """Called often by the app: a clear verse heard once goes up after a quiet pass with nothing against it."""
        events = Events()
        with self.lock:
            if self.pending and now - self.pending[1] >= PENDING_WAIT:
                self._settle_pending([], [], now, events)
            self._release_chapter(now, events)
        return events

    def _release_chapter(self, now, events):
        """No verse followed the chapter: show verse 1 of it, as before."""
        if self.chapter_hold and now - self.chapter_hold[1] >= CHAPTER_WAIT:
            held, _when, _first = self.chapter_hold
            self.chapter_hold = None
            if self._still_reading(held, now):
                return
            self._report(held, [], now, events)

    def _still_reading(self, held, now):
        """Service 2026-11-22: "Matthew chapter 6 verse 33 ... Matthew chapter 7, to seek first the kingdom" put up
        Matthew 7:1 over 6:33. The words after the misheard chapter were still the verse on screen, so keep it."""
        if self.semantic is None or not self.last_reported or not self.hold_words:
            return False
        shown, when = self.last_reported
        if shown.book != held.ref.book or now - when > STILL_READING_SECONDS:
            return False
        first = Ref(held.ref.book, held.ref.chapter, 1)
        scores = self.semantic.scores(" ".join(self.hold_words), [shown, first])
        return scores[0] >= STILL_READING_SCORE and scores[0] - scores[1] >= STILL_READING_MARGIN

    def process(self, transcript, now, seconds=0.0):
        with self.lock:
            return self._process(transcript, now, seconds)

    def _process(self, transcript, now, seconds=0.0):
        """seconds is how long recognition took; a laptop that slow cannot hear anything twice."""
        events = Events()
        self.can_confirm = seconds < self.window_limit
        self.last_now = now
        if self.context_time is not None and now - self.context_time > self.context_life:
            self.context = None  # that passage is long finished
        sentences = sentences_of(transcript)
        matches = self.finder.find(transcript, context=self.context)
        matches = self._after_book_named(transcript, sentences, matches, now)
        self._settle_pending(matches, sentences, now, events, transcript)
        self._verse_coming(matches, now)
        self._release_chapter(now, events)
        last_named = max((i for i, m in enumerate(matches) if m.kind not in ("follow", "next")), default=-1)
        for index, match in enumerate(matches):
            if match.kind in ("follow", "next") and index < last_named:
                continue  # said before the speaker moved to a new passage
            match = self._keep_command(match, now)
            if self._accept(match, now):
                if match.kind == "chapter":  # also "when you READ Hebrews chapter 1...": its verse is usually next
                    self._hold_chapter(match, now)
                else:
                    self._report(match, sentences, now, events)
            else:
                self._late_version(match, sentences, events)
        self._translation(sentences, now, events)
        if not matches and self._go_back(transcript, now, events):
            return events
        if self.chapter_hold:
            self.hold_words.append(transcript)
        if self.semantic is not None:
            self._book_guess(transcript, matches, now, events)
        if not matches and self.semantic is not None:
            self._paraphrase(transcript, sentences, now, events)
        return events

    def _after_book_named(self, transcript, sentences, matches, now):
        """'Exodus.' then, a pass later, 'chapter 14 verse 14': that chapter is in Exodus, not the passage on screen."""
        for sentence in sentences:
            book = self.finder.book_alone(sentence)
            if book:
                self.book_named = (book, now)
        if not self.book_named or now - self.book_named[1] > BOOK_NAMED_SECONDS:
            return matches
        book = self.book_named[0]
        if any(m.kind not in ("follow", "next") for m in matches):
            self.book_named = None  # a full reference was said; the book on its own is finished
            return matches
        if not re.search(r"\bchapters?\b", transcript, re.IGNORECASE):
            return matches  # "verse 3" alone still belongs to the passage on screen
        here = Ref(book, 1, 1)
        rebased = [m for m in self.finder.find(transcript, context=here) if m.kind == "follow"]
        if not rebased:  # "Exodus." ... "Chapter 14.": the book was just named, so a bare chapter belongs to it
            rebased = [m for m in self.finder.find("go to " + transcript, context=here) if m.kind == "follow"]
        if rebased:
            self.book_named = None
            return [replace(m, clear=True) for m in rebased]
        return matches

    def _verse_coming(self, matches, now):
        """'2 Kings 5, from about 22 to 27' heard once: its verse is on the way, so keep waiting (up to a limit)."""
        if not self.chapter_hold:
            return
        held, _when, first = self.chapter_hold
        if now - first < CHAPTER_WAIT_MOST and any(
                m.kind != "chapter" and (m.ref.book, m.ref.chapter) == (held.ref.book, held.ref.chapter) for m in matches):
            self.chapter_hold = (held, now, first)

    def _hold_chapter(self, match, now):
        if not self.chapter_hold or self.chapter_hold[0].ref != match.ref:
            self.chapter_hold = (match, now, now)
            self.hold_words = []
        self.seen_chapters[(match.ref.book, match.ref.chapter)] = now  # its repeats in the next passes are the same

    def _dropped_digit(self, match, now):
        """'John 18:37' on screen, then a pass hears 'John 8:37': one digit lost, not a new verse."""
        if not self.last_reported or match.triggered or match.kind in ("follow", "next", "chapter", "command"):
            return False  # "Media, give me Genesis 1 verse 1" is never a mishearing
        ref, when = self.last_reported
        if now - when > DROPPED_DIGIT_SECONDS or ref.book != match.ref.book or ref == match.ref:
            return False

        def shorter(old, new):
            old, new = str(old), str(new)
            return len(new) == len(old) - 1 and any(old[:i] + old[i + 1:] == new for i in range(len(old)))
        new = match.ref
        return (new.verse == ref.verse and shorter(ref.chapter, new.chapter)) or (
            new.chapter == ref.chapter and shorter(ref.verse, new.verse))

    def _near(self, ref):
        """'Verse 18' while Matthew 2:17-22 is being read is expected; a jump to verse 2 must be heard twice."""
        if self.passage and (ref.book, ref.chapter) == (self.passage.book, self.passage.chapter) and \
                self.passage.verse <= ref.verse <= self.passage.end_verse + 1:
            return True
        here = self.context
        return bool(here) and (ref.book, ref.chapter) == (here.book, here.chapter) and \
            abs(ref.verse - here.verse) <= NEAR_VERSES

    def _lost_number(self, match, now):
        """'First John chapter 1' then a window that cut off 'First' and heard 'John chapter 1': same passage."""
        if not self.last_reported:
            return False
        ref, when = self.last_reported
        return (now - when < NUMBER_CUT_SECONDS and ref.book.endswith(" " + match.ref.book)
                and ref.chapter == match.ref.chapter and (match.kind == "chapter" or ref.verse == match.ref.verse))

    def _settle_pending(self, matches, sentences, now, events, transcript=""):
        if not self.pending:
            return
        held, when = self.pending
        self.pending = None
        if now - when > self.confirm_gap or any(m.ref == held.ref for m in matches):
            return  # too old, or heard again now and confirmed the normal way
        if CHANGE_OF_MIND.search(transcript):
            return  # "no wait", "actually": the pastor may have taken it back
        if any(m.kind not in ("follow", "next") and m.ref.book != held.ref.book for m in matches) or any(
                m.ref.book == held.ref.book and m.ref.chapter != held.ref.chapter for m in matches):
            return  # this pass heard a different verse: the first hearing was probably wrong
        if any(m.ref.book == held.ref.book and m.ref.chapter == held.ref.chapter and m.kind != "chapter"
               for m in matches):
            return  # same chapter, different verse number: unsure which, wait for it to be said again
        if self._fresh(held.ref, now, self.command_cooldown if held.kind == "command" else self.cooldown):
            self._report(held, sentences, now, events)

    def _keep_command(self, match, now):
        """'Turn with me to' can scroll out of the window before the reference is confirmed; it was still a command."""
        if match.triggered:
            self.commands[match.ref] = (now, match)
            return match
        when, earlier = self.commands.get(match.ref, (-1e9, None))
        if earlier and now - when <= self.confirm_gap and earlier.confidence - match.confidence == SCORE_COMMAND:
            return replace(match, kind=earlier.kind, triggered=True, confidence=earlier.confidence)
        return match

    def _go_back(self, transcript, now, events):
        """'Go back to the first one': found by what was on screen, never by matching the words."""
        asked = GO_BACK.search(transcript)
        if not asked or len(self.recent) < 2:
            return False
        if self._confirmed("go back", now) and self._fresh("go back", now, self.command_cooldown):
            ref, code = self.recent[0 if asked[1].lower() == "first" else -2]
            events.found.append(Found(ref, "command", translation=code, triggered=True))
            self.context, self.context_time, self.current = ref, now, code
        return True

    def _accept(self, match, now):
        if self._lost_number(match, now) or self._dropped_digit(match, now):
            return False
        if match.kind == "chapter" and self._chapter_recent(match.ref, now):
            return False  # a window cut mid-phrase; the full reference was already reported
        # Everything must be heard in two windows; one misheard pass ("Proverbs 3-8") must never reach the screen.
        clear = match.clear and (match.kind != "follow" or self._near(match.ref))
        if not self._confirmed(match.ref, now) and not clear:
            if match.confidence >= SINGLE_HEARING_SCORE and match.kind in PENDING_KINDS:
                self.pending = (match, now)
            return False
        explicit = match.kind in ("command", "next") or (match.kind == "follow" and match.triggered)
        if match.kind == "next":
            if now - self.last_next < self.next_gap:
                return False
            self.last_next = now
        if match.ref in self.suggested and (match.clear or match.confidence >= AUTO_THRESHOLD):
            self.seen[match.ref] = now  # "Philemon chapter one" was only suggested; "...chapter 1 verse 1" now clear
            return True
        return self._fresh(match.ref, now, self.command_cooldown if explicit else self.cooldown)

    def _report(self, match, sentences, now, events):
        same_passage = match.kind in ("follow", "next")
        named = self._version_named_with(match.ref, sentences, events)
        code = named or (self.current if same_passage else self.default)
        triggered = match.confidence >= AUTO_THRESHOLD or match.clear
        (self.suggested.add if not triggered else self.suggested.discard)(match.ref)
        self.last_reported = (match.ref, now)
        self.chapter_hold = None  # the verse came (or another passage did): the chapter alone is done
        ref = match.ref
        if ref.end_verse and ref.end_verse > ref.verse:
            self.passage = ref
        elif self.passage and (ref.book, ref.chapter) != (self.passage.book, self.passage.chapter):
            self.passage = None  # moved on to another passage
        if triggered:
            self.current = code
        events.found.append(Found(match.ref, match.kind, translation=code, triggered=triggered))
        self.context, self.context_time = match.ref, now
        self.seen_chapters[(match.ref.book, match.ref.chapter)] = now

    def _late_version(self, match, sentences, events):
        """Whisper can finish 'Jude 1 verse 7' a window before '...in the Amplified' arrives."""
        if match.ref == self.context:
            named = self._version_named_with(match.ref, sentences, events)
            if named and named != self.current:
                self.current = events.translation = named

    def _installed_name(self, code):
        """Spoken "Yoruba" or "Bibeli Mimo" means the church's own Bibeli Mimo (BM) when that file is installed,
        otherwise the open Yoruba Bible (YOR)."""
        if code in ("YOR", "BM"):
            return next((c for c in ("BM", "YOR") if c in self.library.codes), code)
        return code

    def _version_named_with(self, ref, sentences, events):
        for sentence in sentences:
            code = self._installed_name(requested_translation(sentence))
            if code and any(m.ref == ref for m in self.finder.find(sentence, context=self.context)):
                if code in self.library.codes:
                    return code
                events.missing_translation = code
        return None

    def _translation(self, sentences, now, events):
        """A version named on its own, like 'let's see that in Amplified', changes the current passage."""
        spoken = " ".join(s for s in sentences if not self.finder.find(s, context=self.context))
        code = self._installed_name(requested_translation(spoken))
        if not code or now - self.last_voice.get(code, -1e9) < self.voice_cooldown or not self._confirmed(code, now):
            return
        self.last_voice[code] = now
        if code not in self.library.codes:
            events.missing_translation = code
        elif code != self.current:
            self.current = code
            events.translation = code

    def _confirmed(self, item, now):
        """True once the same thing has been heard in two separate windows."""
        before = self.heard.get(item, -1e9)
        self.heard[item] = now
        return not self.can_confirm or (0 < now - before <= self.confirm_gap)

    def _chapter_recent(self, ref, now):
        return now - self.seen_chapters.get((ref.book, ref.chapter), -1e9) < self.cooldown

    def _fresh(self, ref, now, cooldown=None):
        if now - self.seen.get(ref, -1e9) < (self.cooldown if cooldown is None else cooldown):
            return False
        self.seen[ref] = now
        return True

    def _book_guess(self, transcript, matches, now, events):
        self.recent_words = [(t, w) for t, w in self.recent_words if now - t <= BOOK_GUESS_SECONDS] + [(now, transcript)]
        numbers = BOOKLESS.search(transcript)
        if matches:
            self.bookless = None  # a reference came through properly
        elif numbers:
            chapter, verse = int(numbers[1]), int(numbers[2])
            if not self.bookless or self.bookless[:2] != (chapter, verse):
                self.bookless = (chapter, verse, now)
        if not self.bookless:
            return
        chapter, verse, when = self.bookless
        if now - when > BOOK_GUESS_SECONDS:
            self.bookless = None
            return
        words = " ".join(w for t, w in self.recent_words if t >= when - BOOK_GUESS_SECONDS)
        words = BOOKLESS.sub(" ", words)
        candidates = [Ref(book, chapter, verse) for book, chapters in self.library.shape.items()
                      if verse <= chapters.get(chapter, 0)]
        if len(words.split()) < self.min_words or not candidates:
            return
        scores = sorted(zip(self.semantic.scores(words, candidates), candidates), key=lambda p: -p[0])
        best, ref = scores[0]
        runner_up = scores[1][0] if len(scores) > 1 else 0.0
        if best >= BOOK_GUESS_SCORE and best - runner_up >= BOOK_GUESS_MARGIN and self._fresh(ref, now):
            self.bookless = None
            self.suggested.add(ref)
            events.found.append(Found(ref, "book_guess", best, self.default))

    def _paraphrase(self, transcript, sentences, now, events):
        if len(transcript.split()) < self.min_words:
            return
        cued = bool(CUE.search(transcript))
        # The window also holds earlier talk; the latest full-length sentence is the cleanest query.
        spoken = next((s for s in reversed(sentences) if len(s.split()) >= self.min_words), transcript)
        query = LEAD_IN.sub("", spoken)
        limit = self.cued_threshold if cued else self.threshold
        accepted = [c for c in self.semantic.search(query) if c.score >= limit]
        if not accepted or not self._confirmed(accepted[0].ref, now):
            return
        if now - self.seen.get(accepted[0].ref, -1e9) < self.cooldown:
            return  # the same words are still in the listening window
        # Worst first, so the best guess ends up newest in the list.
        for candidate in reversed(accepted):
            if self._fresh(candidate.ref, now):
                self.suggested.add(candidate.ref)  # only a suggestion: the reference said clearly still goes up
                events.found.append(Found(candidate.ref, "paraphrase", candidate.score, self.default))
