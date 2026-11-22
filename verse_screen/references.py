"""Find Bible references in speech-recognition text.

A book name only counts when a number follows it, so "John went home" is ignored.
Every result is checked against the real chapter and verse counts.
"""
import json
import re
from dataclasses import dataclass, replace

import jellyfish
from rapidfuzz import fuzz

from .bible import BOOKS, ROOT, SINGLE_CHAPTER, Ref

CORRECTIONS_PATH = ROOT / "accent_corrections.json"

UNITS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
         "tree": 3, "wan": 1, "won": 1}
TEENS = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
         "seventeen": 17, "eighteen": 18, "nineteen": 19, "tirteen": 13}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
        "ninety": 90, "tirty": 30, "fourty": 40}
ORDINALS = {"first": 1, "second": 2, "third": 3, "1st": 1, "2nd": 2, "3rd": 3}

SPOKEN_ALIASES = {
    "Psalms": ["psalm", "psalms", "salm", "salms"],
    "Song of Solomon": ["song of solomon", "songs of solomon", "song of songs", "canticles"],
    "Revelation": ["revelation", "revelations", "the revelation"],
    "Acts": ["acts", "acts of the apostles", "act of the apostles"],
    "Ecclesiastes": ["ecclesiastes", "ecclesiastics"],
    "Philippians": ["philippians", "phillipians"],
}
TYPED_ABBREVIATIONS = {
    "gen": "Genesis", "ex": "Exodus", "exo": "Exodus", "lev": "Leviticus", "num": "Numbers", "deut": "Deuteronomy",
    "josh": "Joshua", "judg": "Judges", "neh": "Nehemiah", "ps": "Psalms", "psa": "Psalms", "prov": "Proverbs",
    "eccl": "Ecclesiastes", "song": "Song of Solomon", "isa": "Isaiah", "jer": "Jeremiah", "lam": "Lamentations",
    "ezek": "Ezekiel", "dan": "Daniel", "hos": "Hosea", "obad": "Obadiah", "mic": "Micah", "nah": "Nahum",
    "hab": "Habakkuk", "zeph": "Zephaniah", "hag": "Haggai", "zech": "Zechariah", "mal": "Malachi",
    "matt": "Matthew", "mt": "Matthew", "mk": "Mark", "lk": "Luke", "jn": "John", "rom": "Romans",
    "gal": "Galatians", "eph": "Ephesians", "phil": "Philippians", "col": "Colossians", "heb": "Hebrews",
    "jas": "James", "rev": "Revelation",
}
NUMBERED_ABBREVIATIONS = {"sam": "Samuel", "kgs": "Kings", "chr": "Chronicles", "cor": "Corinthians",
                          "thess": "Thessalonians", "tim": "Timothy", "pet": "Peter", "jn": "John"}
# Chapter with no verse is only trusted for names that are not everyday words.
AMBIGUOUS_ALONE = {"Mark", "Job", "Acts", "Numbers", "Judges", "John", "James", "Luke", "Matthew", "Daniel", "Ruth",
                   "Amos", "Joel", "Micah", "Titus", "Esther", "Joshua", "Jude", "Hosea", "Jonah", "Ezra"}
# Real words that fuzzy matching must never turn into a book.
FUZZY_BLOCK = {"number", "judge", "judged", "market", "markets", "marked", "roman", "romance's", "chapter", "verses",
               "version", "hebrew", "psalmist", "kingdom", "jobless", "actor", "action", "people", "genes", "joshua's",
               "general", "exactly", "excess", "matter", "master", "daniels", "lament", "isaac", "jonas", "johnson"}

JOIN_BLOCK = {"the", "and", "book", "from", "see", "read", "turn", "open", "with", "for"}

TOKEN = re.compile(r"\d+(?:st|nd|rd|th)?|[a-z]+(?:'[a-z]+)?|[:\-\u2013\u2014|]")
FILLERS = {"from", "at", "in", "starting", "the", "and", "of", "|"}
VERSE_WORDS = {"verse", "verses", "vs", "vss", "versus", "v", "vers", "vas", "vias", "via"}
# A reference is a command when walking back through filler words reaches one of these.
WAKE_WORDS = {"media", "medya", "midia", "meedia", "projection", "projectionist", "operator"}
ACTION_WORDS = {"give", "project", "show", "display", "bring", "put", "open", "throw", "flash", "pull", "turn", "look",
                "go", "read"}
COMMAND_FILLER = {"the", "book", "of", "to", "at", "us", "me", "up", "please", "can", "could", "you", "i", "let", "let's",
                  "lets", "for", "now", "quickly", "again", "team", "department", "on", "screen", "that", "this", "out",
                  "with", "your", "our", "bibles", "bible"}
PHONETIC_THRESHOLD = 0.85
JUMP_WORDS = {"go", "turn", "look", "jump", "move", "skip"}  # allowed before a bare number: "let's go to 18"
SOFT_WORDS = {"and", "now", "okay", "ok", "so", "then", "yes", "please"}  # may surround a stand-alone "verse 2"
SENTENCE_END = "|"
# Confidence scoring. A verse reaches the screen on its own only at AUTO_THRESHOLD or above.
AUTO_THRESHOLD = 80
SCORE_PASSAGE, SCORE_COMMAND, SCORE_STRUCTURE, SCORE_STANDALONE, PENALTY_CHAT = 50, 30, 20, 30, 40
# A Bible book named with its numbers is scripture whatever words the pastor wraps around it, so it shows on its own.
SCORE_NAMED = 30
NAMED_KINDS = {"exact", "fuzzy", "phonetic", "chapter"}
MINIMUM_SCORE = 50
STRUCTURE_WORDS = {"chapter", "chapters", ":"}
UNITS_AFTER_NUMBER = {"years", "year", "old", "o'clock", "am", "pm", "naira", "kobo", "dollars", "dollar", "pounds",
                      "percent", "minutes", "minute", "hours", "hour", "days", "day", "weeks", "week", "months", "month",
                      "people", "men", "women", "children", "times", "kilometres", "km", "miles"}
NOUNS_BEFORE_NUMBER = {"room", "page", "number", "bus", "age", "house", "flat", "plot", "year"}
RANGE_WORDS = {"to", "through", "till", "until", "-", "\u2013", "\u2014", "and"}
NOT_VERSE_WORDS = FILLERS | RANGE_WORDS | {"or", "for", "chapter", "chapters", ":"}
# The speaker changing their mind. "Wait" or "no" alone are normal Bible words ("they that wait upon the Lord",
# "no man cometh"), so those count only when a new reference or a clear move to one follows.
FIRM_CORRECTIONS = (("no", "wait"), ("wait", "no"), ("wait", "wait"), ("no", "no"), ("scratch", "that"), ("my", "mistake"),
                    ("on", "second", "thought"), ("not", "that", "one"), ("change", "that"))
SOFT_CORRECTIONS = (("no",), ("nope",), ("actually",), ("sorry",), ("rather",), ("instead",), ("wait",), ("correction",),
                    ("pardon",), ("i", "mean"), ("make", "it"))
HESITATIONS = {"um", "uh", "oh", "er"}
CORRECTION_BRIDGE = COMMAND_FILLER | ACTION_WORDS | JUMP_WORDS | HESITATIONS | {"make", "it", "it's", "its", "that's", "try", "mean"} | {
    word for phrase in FIRM_CORRECTIONS + SOFT_CORRECTIONS for word in phrase}
MOVES_TO_PASSAGE = {("go", "to"), ("go", "back"), ("turn", "to"), ("turn", "with"), ("look", "at"), ("open", "to"),
                    ("move", "to"), ("jump", "to"), ("start", "with"), ("start", "from"), ("start", "at")}
NOT_A_PASSAGE = {"it", "this", "that", "what", "how", "why", "these", "those"}


@dataclass(frozen=True)
class Match:
    ref: Ref
    kind: str  # exact | fuzzy | phonetic | chapter | follow | next | command
    triggered: bool = False  # the pastor directed this at the media team
    confidence: int = 0  # 0-100; see AUTO_THRESHOLD
    start: int = 0  # token position in the text it was read from
    end: int = 0
    clear: bool = False  # book named with "chapter"/"verse"/":": goes up from the first hearing (owner 2026-11-11)


NUMBERED_BASES = r"(?:samuel|kings|chronicles|corinthians|thessalonians|timothy|peter|john)"
# Heard 2026-11-11 for "First John": "4th John", "Prost. John". There is no fourth John, so these mean first.
MISHEARD_FIRST = re.compile(r"\b(?:4th|fourth|forth|fast|frost|prost|fest|fust|faust)\b[,.]?\s+(?=" + NUMBERED_BASES + r"\b)")
NUMBER_WORDS = r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\d+)"
# How "verse N" came through a Nigerian accent on 2026-11-11: "Vastu!", "Vastry!", "That's three.", "That's five."
GLUED_VERSE = [(re.compile(r"\b(?:vastu|vasto|vastoo|verstu|vestu|versetu)\b"), "verse 2"),
               (re.compile(r"\b(?:vastry|vastri|vastree|verstry|vestry)\b"), "verse 3"),
               (re.compile(r"\b(?:vasfo|vasfour|versfo)\b"), "verse 4"),
               (re.compile(r"\b(?:vasfive|vasfaif)\b"), "verse 5")]
SPLIT_VERSE = re.compile(r"\b(?:vast|vas|vers|ves|verso)\s+(?=" + NUMBER_WORDS + r"\b)")
# "That's three." said alone is "verse three"; only as a whole short sentence, so normal talk is left alone.
THATS_VERSE = re.compile(r"(^|[.!?]\s+)(?:okay,?\s+)?(?:that's|thats|that is|dats)\s+(" + NUMBER_WORDS + r")(?=\s*[.!?]*\s*$|\s*[.!?])")
# "Pause to join chapter 1", "Second, join chapter 1": join = John before a chapter number or after an ordinal.
FIRST_JOHN = re.compile(r"\b(?:pause|pours|paws|pass)\s+(?:to\s+)?(?=(?:join|john)\b)")
ORDINAL_JOIN = re.compile(r"\b(first|second|third|1st|2nd|3rd|1|2|3)[,]?\s+join\b")
JOIN_CHAPTER = re.compile(r"\bjoin\b(?=,?\s+(?:chapter|\d+)\b)")


_BOOK_WORDS = "|".join(sorted({re.escape(b.lower().split(" ", 1)[-1]) for b in BOOKS} | {"psalm"}, key=len, reverse=True))
# 2026-11-11: "Matthew chapter 6 verse 1" came through as "Matthew, Genesis, 6, verse 1." and showed Genesis 6:1.
# "Genesis" straight after another book, with the numbers after it, is the word "chapter".
GENESIS_AS_CHAPTER = re.compile(r"\b(" + _BOOK_WORDS + r")\b[,.]?\s+genesis\b(?=[,.]?\s+(?:" + NUMBER_WORDS[3:-1] +
                                r"|verse|vs)\b)")


# "Matthew, Shaptah, 2, verse 1." (2026-11-11): one unknown word between a book and "N verse M" is "chapter".
WORD_AS_CHAPTER = re.compile(r"\b(" + _BOOK_WORDS + r")\b[,.]?\s+([a-z]+)[,.]?\s+(?=(?:" + NUMBER_WORDS[3:-1] +
                             r")[,.]?\s+(?:verse|verses|vs|v)\b)")
# "Matthew, one of us one." = "Matthew 1 vs 1": "of us" is how "vs" came through.
OF_US_VERSE = re.compile(r"\b(" + _BOOK_WORDS + r")\b[,.]?\s+(" + NUMBER_WORDS[3:-1] + r")\s+(?:of us|abus|of verse)\s+(?="
                         + NUMBER_WORDS + r"\b)")
# "1 Samuel chapter 2 verse 9" came through as "For summary chapter 2 verse 9" (sermon test 2026-11-21) and showed
# Proverbs 2:9, the book on screen before. Only before "chapter" or "N verse", so "in summary, 3 points" is left alone.
SUMMARY_SAMUEL = re.compile(r"\b(?:(for|first|1st|fast|second|2nd)\s+)?summary\b(?=[,.]?\s+(?:chapter\b|\d+[,.]?\s+"
                            r"(?:verse|verses|vs)\b))")
# Parakeet heard "1 Samuel chapter 2 verse 9" as "First Psalm chapter 2" and "1 Psalm 8 chapter 2 verse 9" (sermon test
# 2026-11-21). There is no first or second Psalms, so a number before "Psalm" means Samuel.
ORDINAL_PSALM = re.compile(r"\b(first|1st|1|second|2nd|2)\s+psalms?\b(?:\s+8\b)?(?=[,.]?\s+chapter\b|[,.]?\s+\d+[,.]?\s+"
                           r"(?:verse|verses|vs)\b)")
# Service 2026-11-22: "Daniel chapter 6" came through as "Darling chapter C", "Datman chapter 6 verse 3", "Darnet",
# and "Joshua chapter 24 verse 15" as "Losson / Joseph / closure chapter 24 verse 15". Only straight before
# "chapter" and a number, which ordinary talk never does.
BOOK_BEFORE_CHAPTER = [
    (re.compile(r"\b(?:darling|darnet|darnit|darn it|datman|dat man|dannel|danyel)\b[,.]?\s+(?=chapter\b)"), "daniel "),
    (re.compile(r"\b(?:losson|lawson|lasson|closure|joseph)\b[,.]?\s+(?=chapter\s+(?:" + NUMBER_WORDS[3:-1] + r")\b)"),
     "joshua "),
]
# "Daniel chapter 6" heard as "Daniel chapter C" / "chapter sees": after a book and "chapter", see/sees/C is six.
CHAPTER_SIX = re.compile(r"\b(" + _BOOK_WORDS + r")\b[,.]?\s+chapter\s+(?:c|see|sees|seas|sea)\b")
_KEEP_BETWEEN = {"chapter", "chapters", "and", "or", "to", "in", "is", "was", "says", "said", "from", "the", "book"}


def _word_as_chapter(match):
    word = match[2]
    if word in _KEEP_BETWEEN or re.fullmatch(_BOOK_WORDS + "|" + NUMBER_WORDS[3:-1], word):
        return match[0]
    return f"{match[1]} chapter "


def _fix_misheard(text):
    for pattern, book in BOOK_BEFORE_CHAPTER:
        text = pattern.sub(book, text)
    text = CHAPTER_SIX.sub(lambda m: f"{m[1]} chapter six", text)
    text = SUMMARY_SAMUEL.sub(lambda m: "second samuel" if m[1] in ("second", "2nd") else "first samuel", text)
    text = ORDINAL_PSALM.sub(lambda m: "second samuel" if m[1] in ("second", "2nd", "2") else "first samuel", text)
    text = WORD_AS_CHAPTER.sub(_word_as_chapter, text)
    text = OF_US_VERSE.sub(lambda m: f"{m[1]} {m[2]} verse ", text)
    text = GENESIS_AS_CHAPTER.sub(lambda m: f"{m[1]} chapter" if m[1] != "genesis" else m[0], text)
    for pattern, said in GLUED_VERSE:
        text = pattern.sub(said, text)
    text = SPLIT_VERSE.sub("verse ", text)
    text = THATS_VERSE.sub(lambda m: f"{m[1]}verse {m[2]}", text)
    text = FIRST_JOHN.sub("first ", text)
    text = ORDINAL_JOIN.sub(lambda m: f"{m[1]} john", text)
    return JOIN_CHAPTER.sub("john", text)


def tokenize(text):
    text = MISHEARD_FIRST.sub("first ", _fix_misheard(text.lower()))
    text = re.sub(r"\bv\.?\s?s\b\.?", "vs", text.replace("\u2019", "'"))  # Whisper can write "V.S." or "V S"
    text = re.sub(r"(?<=\d)x(?=\d)", ":", text)  # "Galatians 5x22"
    text = re.sub(r"[.!?]+(?=\s|$)", f" {SENTENCE_END} ", text)
    raw = TOKEN.findall(text)
    tokens, index = [], 0
    while index < len(raw):
        value, index = _read_number(raw, index)
        tokens.append(value)
    return tokens


def _read_number(raw, i):
    """Merge number words into one int, but keep 'three sixteen' as two numbers."""
    word = raw[i]
    if word in ORDINALS:
        return word if word.isalpha() else {1: "first", 2: "second", 3: "third"}[ORDINALS[word]], i + 1
    if re.fullmatch(r"\d+(st|nd|rd|th)?", word):
        return int(re.match(r"\d+", word)[0]), i + 1
    if word == "a" and i + 1 < len(raw) and raw[i + 1] == "hundred":
        return _after_hundred(raw, i + 2, 100)
    if word == "hundred":
        return _after_hundred(raw, i + 1, 100)
    if word in UNITS:
        if i + 1 < len(raw) and raw[i + 1] == "hundred":
            return _after_hundred(raw, i + 2, UNITS[word] * 100)
        if i + 2 < len(raw) and raw[i + 1] == "oh" and raw[i + 2] in UNITS:  # "one oh five"
            return UNITS[word] * 100 + UNITS[raw[i + 2]], i + 3
        return UNITS[word], i + 1
    if word in TEENS:
        return TEENS[word], i + 1
    if word in TENS:
        if i + 1 < len(raw) and raw[i + 1] in UNITS:
            return TENS[word] + UNITS[raw[i + 1]], i + 2
        return TENS[word], i + 1
    return word, i + 1


def _contains(words, phrases):
    return any(tuple(words[i:i + len(phrase)]) == phrase for phrase in phrases for i in range(len(words)))


def _is_correction(between):
    """Only correction talk between two references, as opposed to the sermon carrying on."""
    words = [word for word in between if word != SENTENCE_END]
    return all(word in CORRECTION_BRIDGE for word in words) and _contains(words, FIRM_CORRECTIONS + SOFT_CORRECTIONS)


def _takes_back(after):
    """Do the words straight after a reference withdraw it? Needed when the replacement has no chapter yet."""
    words = [word for word in after if word != SENTENCE_END]
    while words and words[0] in HESITATIONS:
        words.pop(0)
    if any(tuple(words[:len(phrase)]) == phrase for phrase in FIRM_CORRECTIONS):
        return True
    soft = next((phrase for phrase in SOFT_CORRECTIONS if tuple(words[:len(phrase)]) == phrase), None)
    if not soft:
        return False
    rest = words[len(soft):]
    while rest and rest[0] in ("let's", "lets", "let", "us", "wait", "no", "sorry"):
        rest.pop(0)
    if tuple(rest[:2]) not in MOVES_TO_PASSAGE:
        return False
    target = rest[2:3]
    return not (target and (target[0] in NOT_A_PASSAGE or (target[0] == "the" and rest[3:4] != ["book"])))


def _firm_correction_before(tokens, start):
    first = start
    while first > 0 and (tokens[first - 1] in CORRECTION_BRIDGE or tokens[first - 1] == SENTENCE_END):
        first -= 1
    return _contains([word for word in tokens[first:start] if word != SENTENCE_END], FIRM_CORRECTIONS)


def _as_command(match):
    kind = "command" if match.kind in ("exact", "fuzzy", "phonetic") else match.kind
    return replace(match, kind=kind, triggered=True, confidence=max(match.confidence, AUTO_THRESHOLD))


def _after_hundred(raw, i, value):
    j = i + 1 if i < len(raw) and raw[i] == "and" else i
    if j < len(raw) and (raw[j] in TENS or raw[j] in TEENS or raw[j] in UNITS):
        extra, end = _read_number(raw, j)
        return value + extra, end
    return value, i


def _teen(number):
    """A Nigerian accent stresses the end of "thirteen", so it is often written as "30". Used only when the number
    heard does not exist in that book, so a real "verse 30" is never changed."""
    return number // 10 + 10 if number in (30, 40, 50, 60, 70, 80, 90) else None


def _build_aliases():
    aliases = {}
    for book in BOOKS:
        match = re.fullmatch(r"([123]) (.+)", book)
        if match:
            number, name = int(match[1]), match[2].lower().split()
            ordinal = {1: "first", 2: "second", 3: "third"}[number]
            aliases[(number, *name)] = book
            aliases[(ordinal, *name)] = book
        else:
            aliases[tuple(book.lower().split())] = book
    for book, spoken in SPOKEN_ALIASES.items():
        for phrase in spoken:
            aliases[tuple(tokenize(phrase))] = book
    return aliases


class ReferenceFinder:
    def __init__(self, library, corrections_path=CORRECTIONS_PATH):
        self.library = library
        self.aliases = _build_aliases()
        self.typed = dict(self.aliases)
        for short, book in TYPED_ABBREVIATIONS.items():
            self.typed[(short,)] = book
        for short, name in NUMBERED_ABBREVIATIONS.items():
            for number in (1, 2, 3):
                if f"{number} {name}" in BOOKS:
                    self.typed[(number, short)] = f"{number} {name}"
        self._add_typed_prefixes()
        self.all_stems = {}
        for book in BOOKS:
            stem = book.split()[-1].lower() if book[0].isdigit() else book.lower()
            if " " not in stem:
                self.all_stems[stem] = book
        self.stems = {stem: book for stem, book in self.all_stems.items() if len(stem) >= 5}
        self.trigger_aliases = {}
        self._load_corrections(corrections_path)

    def _add_typed_prefixes(self):
        """Let the operator type any unambiguous start of a book name, like 'esth' or '1 thes'."""
        counts = {}
        for key, book in _build_aliases().items():
            if isinstance(key[-1], str) and len(key) <= 2:
                for size in range(3, len(key[-1])):
                    counts.setdefault((*key[:-1], key[-1][:size]), set()).add(book)
        for key, books in counts.items():
            if len(books) == 1:
                self.typed.setdefault(key, next(iter(books)))

    def _load_corrections(self, path):
        if not path.exists():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        for heard, book in data.get("heard_as_book", {}).items():
            if book in BOOKS:
                self.aliases[tuple(tokenize(heard))] = book
                self.typed[tuple(tokenize(heard))] = book
        for heard, book in data.get("heard_as_book_after_trigger", {}).items():
            if book in BOOKS:
                self.trigger_aliases[tuple(tokenize(heard))] = book

    def book_alone(self, sentence):
        """'Exodus.' or 'Turn to the book of Exodus.': a book named with no numbers yet."""
        words = tuple(t for t in tokenize(sentence) if t != SENTENCE_END and t not in COMMAND_FILLER
                      and t not in ACTION_WORDS and t not in WAKE_WORDS and t not in HESITATIONS)
        return self.aliases.get(words) if 0 < len(words) <= 3 else None

    def find(self, text, context=None, typed=False):
        """Return references in the order spoken. context enables 'verse 17' follow-ups."""
        tokens = tokenize(text)
        aliases = self.typed if typed else self.aliases
        matches, i, read_up_to = [], 0, 0
        while i < len(tokens):
            found = self._at_position(tokens, i, aliases, typed)
            if not found and not self._unread_book_before(tokens, i, read_up_to):
                found = self._follow_up(tokens, i, context)
            if not found:
                i += 1
                continue
            match, end = found
            match = self._assess(tokens, i, end, match)
            if typed or match.confidence >= MINIMUM_SCORE:
                matches.append(match)
                context = match.ref
                read_up_to = end
            i = end
        return matches if typed else self._apply_corrections(tokens, matches)

    def _apply_corrections(self, tokens, matches):
        """'John 3:16, no wait, Genesis 1:1': drop what was taken back; a reference given as the correction is a command."""
        kept = []
        for index, match in enumerate(matches):
            following = matches[index + 1] if index + 1 < len(matches) else None
            if (following and _is_correction(tokens[match.end:following.start])) or _takes_back(tokens[match.end:match.end + 10]):
                continue
            answers_earlier = index > 0 and _is_correction(tokens[matches[index - 1].end:match.start])
            kept.append(_as_command(match) if answers_earlier or _firm_correction_before(tokens, match.start) else match)
        return kept

    def _unread_book_before(self, tokens, i, read_up_to):
        """'Give me Prophets 18 verse 11': the book was misheard, so this must not become verse 11 of the old passage."""
        before = i - 1
        if before < read_up_to or tokens[i] not in ({"chapter"} | VERSE_WORDS):
            return False
        word = tokens[before]
        if isinstance(word, int):
            return True
        return (word not in COMMAND_FILLER and word not in WAKE_WORDS and word not in ACTION_WORDS
                and word not in SOFT_WORDS and word not in JUMP_WORDS and word not in ("back", SENTENCE_END)
                and self._triggered(tokens, before))

    @staticmethod
    def _triggered(tokens, start):
        j = start - 1
        while j >= 0 and tokens[j] in COMMAND_FILLER:
            j -= 1
        if j < 0:
            return False
        word = tokens[j]
        if word in WAKE_WORDS or word in ACTION_WORDS:
            return True
        before = tokens[max(0, j - 2):j]
        return (word == "have" and before[:1] in (["can"], ["could"])) or (
            word == "see" and bool(before) and before[-1] in ("me", "us", "lets", "let's"))

    def _assess(self, tokens, start, end, match):
        """Mark commands and score how sure we are that this is scripture and not conversation."""
        explicit = self._triggered(tokens, start)
        kind = "command" if explicit and match.kind in ("exact", "fuzzy", "phonetic") else match.kind
        score = self._score(tokens, start, end, match, explicit)
        said_verse = any(t in STRUCTURE_WORDS or t in VERSE_WORDS for t in tokens[start:end] if isinstance(t, str))
        # Owner 2026-11-11: once the chapter and verse are clear it goes up. "First John chapter 1" (a chapter said
        # with the word "chapter") and "verse 2" after a passage is up count as clear too.
        clear = match.kind == "next" or said_verse and ((match.kind in ("exact", "fuzzy", "phonetic", "chapter") and score >= AUTO_THRESHOLD)
                                or (match.kind == "follow" and score >= MINIMUM_SCORE))
        return Match(match.ref, kind, explicit, score, start, end, clear)

    def _score(self, tokens, start, end, match, explicit):
        if match.kind == "next":
            return 100
        span = tokens[start:end]
        score = SCORE_PASSAGE + (SCORE_COMMAND if explicit else 0)
        if any(t in STRUCTURE_WORDS or t in VERSE_WORDS for t in span if isinstance(t, str)):
            score += SCORE_STRUCTURE
        if match.kind == "follow" and self._standalone(tokens, start, end):
            score += SCORE_STANDALONE
        if self._number_run_after(tokens, end):
            return score - PENALTY_CHAT  # "2 Corinthians 5, 7, 5, 5, 2, 1, 1": the speech engine stuck on numbers
        named_to_media = explicit and match.kind in ("exact", "fuzzy", "phonetic")  # "bus 11" here is a misheard "verse 11"
        if self._is_conversation(tokens, start, end, named_to_media):
            return score - PENALTY_CHAT  # "Mark 5 minutes": numbers that belong to something else get no book bonus
        return score + (SCORE_NAMED if match.kind in NAMED_KINDS else 0)

    @staticmethod
    def _number_run_after(tokens, end, run=3):
        """Three or more bare numbers straight after a reference: not a passage anyone reads out."""
        after = [t for t in tokens[end:end + run + 2] if t not in RANGE_WORDS]
        return len(after) >= run and all(isinstance(t, int) for t in after[:run])

    @staticmethod
    def _standalone(tokens, start, end):
        """True when the sentence is nothing but the reference, like 'Verse 2.'"""
        j = start - 1
        while j >= 0 and tokens[j] in SOFT_WORDS:
            j -= 1
        k = end
        while k < len(tokens) and tokens[k] in SOFT_WORDS:
            k += 1
        before_clear = j < 0 or tokens[j] == SENTENCE_END or isinstance(tokens[j], int)  # after the last reference
        while k < len(tokens) and tokens[k] in COMMAND_FILLER:
            k += 1
        after_clear = k >= len(tokens) or tokens[k] == SENTENCE_END or tokens[k] in WAKE_WORDS or tokens[k] in ACTION_WORDS
        return before_clear and after_clear

    @staticmethod
    def _is_conversation(tokens, start, end, ignore_nouns=False):
        for index in range(start, end):
            if isinstance(tokens[index], int):
                after = tokens[index + 1] if index + 1 < len(tokens) else None
                before = tokens[index - 1] if index else None
                if after in UNITS_AFTER_NUMBER or (before in NOUNS_BEFORE_NUMBER and not ignore_nouns):
                    return True
        return False

    def _at_position(self, tokens, i, aliases, typed):
        triggered = not typed and self._triggered(tokens, i)
        lookup = {**self.typed, **self.trigger_aliases} if triggered else aliases  # "give me gen 1 vs 1"
        for size in range(5, 0, -1):
            book = lookup.get(tuple(tokens[i:i + size]))
            if book:
                result = self._numbers_after(tokens, i + size, book, "exact", typed, triggered)
                if result:
                    return result
        if typed:
            return None
        return self._fuzzy(tokens, i, triggered) or (self._phonetic(tokens, i) if triggered else None)

    def _phonetic(self, tokens, i):
        """After a trigger, accept the Bible book that sounds closest to the heard word."""
        word = tokens[i]
        if not isinstance(word, str) or word in COMMAND_FILLER or not self._number_next(tokens, i + 1):
            return None
        sound = jellyfish.metaphone(word)
        best, best_score = None, 0.0
        for stem, book in self.all_stems.items():
            score = 0.95 if sound and sound == jellyfish.metaphone(stem) else jellyfish.jaro_winkler_similarity(word, stem)
            if score > best_score:
                best, best_score = book, score
        if best_score < PHONETIC_THRESHOLD:
            return None
        book = self._numbered_fuzzy(tokens, i, best)
        return self._numbers_after(tokens, i + 1, book, "phonetic", False, True) if book else None

    def _fuzzy(self, tokens, i, triggered=False):
        word = tokens[i]
        if not isinstance(word, str) or word in FUZZY_BLOCK:
            return None
        for size, heard in ((1, word), (2, self._joined(tokens, i))):
            if not heard or len(heard) < 6 or not self._number_next(tokens, i + size):
                continue
            best, score = None, 0
            for stem in self.stems:
                value = fuzz.ratio(heard, stem)
                if value > score:
                    best, score = stem, value
            if score < 82:
                continue
            book = self._numbered_fuzzy(tokens, i, self.stems[best])
            if book:
                result = self._numbers_after(tokens, i + size, book, "fuzzy", False, triggered)
                if result:
                    return result
        return None

    @staticmethod
    def _joined(tokens, i):
        if i + 1 < len(tokens) and isinstance(tokens[i], str) and isinstance(tokens[i + 1], str):
            if len(tokens[i]) >= 3 and tokens[i] not in JOIN_BLOCK and SENTENCE_END not in tokens[i:i + 2]:  # keep "of Romans" from becoming a mishearing
                return tokens[i] + tokens[i + 1]
        return None

    @staticmethod
    def _number_next(tokens, i):
        return i < len(tokens) and (isinstance(tokens[i], int) or tokens[i] == "chapter")

    @staticmethod
    def _numbered_fuzzy(tokens, i, book):
        if not book[0].isdigit():
            return book
        previous = tokens[i - 1] if i else None
        number = {"first": 1, "second": 2, "third": 3}.get(previous, previous)
        candidate = f"{number} {book[2:]}"
        return candidate if candidate in BOOKS else None

    def _numbers_after(self, tokens, j, book, kind, typed, triggered):
        t = j
        if t + 1 < len(tokens) and tokens[t] == SENTENCE_END and tokens[t + 1] in ("chapter", "chapters"):
            t += 1  # "Exodus. Chapter 14 verse 14.": a pause after the book became a full stop
        said_chapter = t < len(tokens) and tokens[t] in ("chapter", "chapters")
        if said_chapter:
            t += 1
        if (book in SINGLE_CHAPTER and not said_chapter and t + 1 < len(tokens) and tokens[t] in VERSE_WORDS
                and isinstance(tokens[t + 1], int) and self.library.exists(book, 1, tokens[t + 1])):
            return self._finish(tokens, t + 2, book, 1, tokens[t + 1], kind)  # "Jude verse 3", "3rd John verse 2"
        if t >= len(tokens) or not isinstance(tokens[t], int):
            return None
        first = tokens[t]
        t += 1
        if t + 3 < len(tokens) and tokens[t] in ("chapter", "chapters") and tokens[t + 1] == first and \
                tokens[t + 2] in VERSE_WORDS and isinstance(tokens[t + 3], int):
            t += 2  # "Judges 15, chapter 15, verse 9": the chapter said twice, not verse 15
        second, t = self._second_number(tokens, t, triggered)
        if second is None and t < len(tokens) and (tokens[t] == ":" or tokens[t] in VERSE_WORDS):
            return None  # "Jude 1 verse..." was cut off before the verse number
        if book in SINGLE_CHAPTER:
            if second is not None and first != 1:  # "second, John 3:16" is John, not 2 John
                return None
            verse = second if second is not None else first
            if second is None:
                kind = "single"  # one number could still be a cut-off "Jude 1 verse 7"
            if not self.library.exists(book, 1, verse) and _teen(verse):
                verse = _teen(verse)
            return self._finish(tokens, t, book, 1, verse, kind) if self.library.exists(book, 1, verse) else None
        if second is not None and 1 <= second <= 9 and t < len(tokens) and isinstance(tokens[t], int) \
                and 0 <= tokens[t] <= 9 and self.library.exists(book, first, second * 10 + tokens[t]):
            second, t = second * 10 + tokens[t], t + 1  # "verse 1 8" / "verse one eight" is verse 18
        if second is not None:
            for chapter, verse in ((first, second), (_teen(first), second), (first, _teen(second)),
                                   (_teen(first), _teen(second))):
                if chapter and verse and self.library.exists(book, chapter, verse):
                    return self._finish(tokens, t, book, chapter, verse, kind)
            return None
        if first >= 100 and not self.library.exists(book, first, 1):
            glued = self._split_glued(book, first)
            if glued:
                return self._finish(tokens, t, book, glued[0], glued[1], kind)
            return None
        distinctive = book not in AMBIGUOUS_ALONE
        if self.library.exists(book, first, 1) and (said_chapter or distinctive or typed or triggered):
            kind = "chapter" if not typed else kind
            return Match(Ref(book, first, 1), kind), t
        return None

    @staticmethod
    def _second_number(tokens, t, triggered=False):
        if t < len(tokens) and isinstance(tokens[t], int):
            return tokens[t], t + 1
        j, marked = t, False
        while j < len(tokens) and j - t < 5:
            word = tokens[j]
            if word in (":", "-", "\u2013", "\u2014", "chapter") or word in VERSE_WORDS:
                marked = True
            elif word == "from" and j + 1 < len(tokens) and (isinstance(tokens[j + 1], int) or tokens[j + 1] in (
                    "about", "around")):
                marked = True  # "2 Kings 5, from about 22 to 27" (sermon test 2026-11-21)
                if tokens[j + 1] in ("about", "around"):
                    j += 1
            elif word not in FILLERS:
                break
            j += 1
        if marked and j < len(tokens) and isinstance(tokens[j], int):
            return tokens[j], j + 1
        if triggered and t + 1 < len(tokens) and isinstance(tokens[t + 1], int) and isinstance(tokens[t], str) \
                and tokens[t] not in NOT_VERSE_WORDS and tokens[t] not in UNITS_AFTER_NUMBER:
            # "Media, give me Proverbs 18 was 11": an accent can turn "verse" into almost any short word.
            after = tokens[t + 2] if t + 2 < len(tokens) else None
            if after not in UNITS_AFTER_NUMBER:
                return tokens[t + 1], t + 2
        return None, t

    def _split_glued(self, book, number):
        """Whisper often writes 'John 3:16' as 'John 316'."""
        digits = str(number)
        for cut in range(1, len(digits)):
            if digits[cut] != "0" and self.library.exists(book, int(digits[:cut]), int(digits[cut:])):
                return int(digits[:cut]), int(digits[cut:])
        return None

    def _finish(self, tokens, t, book, chapter, verse, kind):
        end_verse, t = self._range_end(tokens, t, verse, book, chapter)
        return Match(Ref(book, chapter, verse, end_verse), kind), t

    def _range_end(self, tokens, t, start, book, chapter):
        if t >= len(tokens) or tokens[t] not in RANGE_WORDS:
            return None, t
        j = t + 1
        if j < len(tokens) and tokens[j] in VERSE_WORDS:
            j += 1
        if j >= len(tokens) or not isinstance(tokens[j], int):
            return None, t
        end = tokens[j]
        limit = 1 if tokens[t] == "and" else 12
        if start < end <= start + limit and self.library.exists(book, chapter, end):
            return end, j + 1
        return None, t

    @staticmethod
    def _jump_before(tokens, i):
        j = i - 1
        while j >= 0 and tokens[j] in COMMAND_FILLER:
            j -= 1
        return 0 <= j < i - 1 and tokens[j] in JUMP_WORDS

    def _follow_up(self, tokens, i, context):
        if context is None:
            return None
        word = tokens[i]
        if word == "next" and i + 1 < len(tokens) and tokens[i + 1] == "verse":
            if context.end_verse and context.end_verse > context.verse:  # "Matthew 2:17-22" is up: next is 18
                return Match(Ref(context.book, context.chapter, context.verse + 1), "next"), i + 2
            after = self.library.step(context, 1)
            return (Match(after, "next"), i + 2) if after.book == context.book else None  # never into another book
        if word == "chapter" and i + 1 < len(tokens) and isinstance(tokens[i + 1], int):
            number, t = self._second_number(tokens, i + 2)
            if number is not None and self.library.exists(context.book, tokens[i + 1], number):
                return self._finish(tokens, t, context.book, tokens[i + 1], number, "follow")
            if number is None and self._triggered(tokens, i) and self.library.exists(context.book, tokens[i + 1], 1):
                return Match(Ref(context.book, tokens[i + 1], 1), "follow"), i + 2  # "go to chapter 5"
            return None
        if isinstance(word, int) and self._jump_before(tokens, i):
            if self.library.exists(context.book, context.chapter, word):
                return self._finish(tokens, i + 1, context.book, context.chapter, word, "follow")
            return None
        if word in VERSE_WORDS and i + 1 < len(tokens) and isinstance(tokens[i + 1], int):
            number = tokens[i + 1]
            if self.library.exists(context.book, context.chapter, number):
                return self._finish(tokens, i + 2, context.book, context.chapter, number, "follow")
        return None
