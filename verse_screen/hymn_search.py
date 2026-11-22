"""Find a hymn the church does not have yet: first in the free hymns that come with the app (works offline), then on
Wikisource, the free library of public-domain texts. Also reads a church's own hymn book from Word or PDF.

Only public-domain words are offered. Modern worship songs are copyrighted; a church shows those under its own
licence (CCLI) by typing or importing them.
"""
import html
import json
import re
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field

from .bible import ROOT
from .hymns import Stanza, parse_lyrics

LIBRARY_PATH = ROOT / "data" / "hymns" / "public_domain.json"
WIKISOURCE = "https://en.wikisource.org/w/api.php"
USER_AGENT = "faithapp-hymn-search/1.0 (free church presentation software)"
LIBRARY_NAME = "Free hymn library"
ONLINE_NAME = "Wikisource"


@dataclass
class Found:
    title: str
    source: str  # LIBRARY_NAME or ONLINE_NAME
    author: str = ""
    stanzas: list = field(default_factory=list)  # filled for the library; fetched on demand for Wikisource
    page: str = ""  # Wikisource page title

    @property
    def label(self):
        by = f" ({self.author})" if self.author else ""
        return f"{self.title}{by}  ·  {self.source}"


def _words(text):
    return re.findall(r"[a-z0-9']+", text.lower().replace("’", "'"))


_library = None


def library():
    global _library
    if _library is None:
        try:
            data = json.loads(LIBRARY_PATH.read_text(encoding="utf-8"))["hymns"]
        except (OSError, ValueError, KeyError):
            data = []
        _library = [Found(h["title"], LIBRARY_NAME, _author(h.get("author", "")),
                          [Stanza(s["label"], s["text"]) for s in h["stanzas"]]) for h in data]
    return _library


def _author(raw):
    """'Lyte, Henry F. (1793-1847)' -> 'Henry F. Lyte'; several authors are split on ';'."""
    names = []
    for part in re.sub(r"\s*\(.*?\)", "", raw).split(";"):
        name = part.strip()
        if "," in name:
            last, first = [p.strip() for p in name.split(",", 1)]
            name = f"{first} {last}"
        if name and name.lower() not in ("none", "unknown", "anonymous", "traditional"):
            names.append(name)
    return " & ".join(names)


STOP = {"the", "a", "an", "and", "of", "to", "in", "my", "o", "oh", "is", "it", "with", "thy", "thee", "thou", "i",
        "me", "we", "us", "our", "be", "for", "on", "all"}


def search_library(query, limit=30):
    """The phrase in the title, the first verse or anywhere, best first; else hymns holding most of its words."""
    wanted = _words(query)
    if not wanted:
        return []
    phrase = " ".join(wanted)
    key = [w for w in wanted if w not in STOP] or wanted
    scored = []
    for hymn in library():
        title = " ".join(_words(hymn.title))
        first = " ".join(_words(hymn.stanzas[0].text)) if hymn.stanzas else ""
        everything = " ".join(_words(" ".join(s.text for s in hymn.stanzas)))
        present = set(title.split()) | set(everything.split())
        share = sum(w in present for w in key) / len(key)
        score = (3 if phrase in title else 0) + (2 if phrase in first else 0) + (1 if phrase in everything else 0)
        if score == 0 and share < 0.8:
            continue
        scored.append((score, share, hymn.title, hymn))
    if any(item[0] for item in scored):  # the words as said were found: only those hymns
        scored = [item for item in scored if item[0]]
    scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return [item[3] for item in scored[:limit]]


def _get(params, timeout):
    url = WIKISOURCE + "?" + urllib.parse.urlencode({**params, "format": "json", "formatversion": 2})
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _search(words, limit, timeout):
    data = _get({"action": "query", "list": "search", "srsearch": words, "srlimit": limit, "srnamespace": 0,
                 "srprop": ""}, timeout)
    return [row["title"] for row in data.get("query", {}).get("search", [])]


NOT_HYMNS = re.compile(r"^(?:bible|webster's revision bible|the holy bible)\b|/chapter \d|letters of", re.I)


def search_online(query, limit=8, timeout=8):
    """Hymn pages on Wikisource for these words, each already checked to hold hymn words (stanzas filled).
    The exact phrase is searched first, as hymns are found by their lines. Raises OSError with no internet."""
    from concurrent.futures import ThreadPoolExecutor

    words = " ".join(query.split())
    if not words:
        return []
    pages = _search(f'"{words}"', limit + 4, timeout)
    if len(pages) < 3:
        pages += [p for p in _search(f"{words} hymn", limit + 4, timeout) if p not in pages]
    candidates = []
    for page in pages:
        if NOT_HYMNS.search(page):
            continue
        name = page.rsplit("/", 1)[-1]
        if re.fullmatch(r"(?:hymn|book|part|section)?\s*\d+", name, re.I):
            name = page  # "Olney Hymns (1840)/Book 3/Hymn 11" means nothing as "Hymn 11"
        candidates.append(Found(name, ONLINE_NAME, page=page))
    candidates = candidates[:5]

    def fill(found):
        try:
            found.stanzas = fetch_online(found, timeout)
        except (OSError, ValueError):
            found.stanzas = []
        return found

    with ThreadPoolExecutor(max_workers=2) as pool:
        checked = list(pool.map(fill, candidates))
    return [f for f in checked if len(f.stanzas) >= 2 and _mentions(f, words)][:limit]


def _mentions(found, words):
    """The page must hold the searched words in its title or verses (search also matches notes and headers)."""
    wanted = set(_words(words))
    text = set(_words(found.title + " " + " ".join(s.text for s in found.stanzas)))
    return len(wanted & text) >= max(1, int(len(wanted) * 0.7))


POEM = re.compile(r'<div[^>]*class="[^"]*\bpoem\b[^"]*"[^>]*>(.*?)</div>', re.S)
VERSE_LIST = re.compile(r'<ol[^>]*class="wst(?:&#95;|_)olist[^"]*"[^>]*>(.*?)</ol>', re.S)
MARKER = re.compile(r"^\s*(chorus|refrain)\s*[:.]?\s*$", re.I)


def _page_text(fragment):
    fragment = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", fragment, flags=re.S)
    fragment = re.sub(r"<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"</(p|div|dd|li|h\d)>", "\n\n", fragment)
    text = html.unescape(re.sub(r"<[^>]+>", "", fragment))
    return text.replace("\u200b", "")


def clean_lyrics(text):
    """Strip what a printed hymnal adds around the words: dots for held notes, '(Last verse: Amen.)', numbers."""
    text = re.sub(r"\s*\.(?:\s*\.){1,}", "", text)  # "It is well . . . with my soul"
    text = re.sub(r"\(\s*last verse[^)]*\)", "", text, flags=re.I)
    text = re.sub(r"(?m)^\s*\d{1,4}\s*[\u2014\u2013-]\s*[A-Z ,'!?]+$", "", text)  # "161-WHEN PEACE LIKE A RIVER"
    lines = [line.strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _stanzas_from(text, lines_per_verse=4):
    """Hymn text from a page -> stanzas. 'Refrain' starts the chorus the first time; after that a bare 'Refrain'
    only means sing it again, which the screen does by itself. Pages that put every line in its own paragraph are
    read line by line, four lines to a verse."""
    paragraphs = [[l for l in block.split("\n") if l.strip()] for block in re.split(r"\n\s*\n", text)]
    paragraphs = [p for p in paragraphs if p]
    single = sum(len(p) == 1 for p in paragraphs) >= 0.8 * max(1, len(paragraphs))
    stanzas, chorus_seen, part, in_chorus = [], False, [], False

    def close():
        nonlocal part, in_chorus
        if part:
            stanzas.append(("Chorus:\n" if in_chorus else "") + "\n".join(part))
        part, in_chorus = [], False

    for paragraph in paragraphs:
        for line in paragraph:
            if MARKER.match(line):
                close()
                if not chorus_seen:
                    chorus_seen, in_chorus = True, True
                continue
            part.append(line)
            if single and len(part) == lines_per_verse:
                close()
        if not single:
            close()
    close()
    return parse_lyrics("\n\n".join(stanzas))


def fetch_online(found, timeout=10):
    """The stanzas of a Wikisource page (its poem or verse list only, so headers and notes are left out)."""
    data = _get({"action": "parse", "page": found.page, "prop": "text", "disablelimitreport": 1}, timeout)
    page = data.get("parse", {}).get("text", "")
    best = []
    for blocks in (VERSE_LIST.findall(page), POEM.findall(page)):
        if blocks:
            stanzas = _stanzas_from(clean_lyrics("\n\n".join(_page_text(b) for b in blocks)))
            if len(stanzas) > len(best):
                best = stanzas
    return best


# ---- a church's own hymn book: Word or PDF, many hymns in one file ----
EXPLICIT = re.compile(r"^\s*(?:hymn|no\.?|number|orin|song)\s*(\d{1,4})[a-z]?\s*[.:)-]?\s*(.*)$", re.I)
BARE = re.compile(r"^\s*(\d{1,4})[a-z]?\s*[.:)-]?\s*(.*)$")
SMALL = {"a", "an", "the", "of", "and", "to", "in", "on", "for", "with", "is", "my", "o", "at", "by", "from", "be"}


def read_docx(path):
    """The text of a .docx (a zip of XML): one line per paragraph, line breaks kept."""
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")
    lines = []
    for paragraph in re.findall(r"<w:p\b.*?</w:p>", xml, re.S):
        paragraph = re.sub(r"<w:(?:br|cr)\b[^>]*/>", "<w:t>\n</w:t>", paragraph)
        lines.append(html.unescape("".join(re.findall(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", paragraph, re.S))))
    return "\n".join(lines)


def read_pdf(path):
    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise RuntimeError("PDF reading is not installed; run setup again with internet") from error
    return "\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)


def _looks_like_title(rest):
    words = re.findall(r"[A-Za-z']+", rest)
    if not words or len(words) > 9 or re.search(r"[,;:.!?]$", rest.strip()):
        return False
    big = [w for w in words if w.lower() not in SMALL]
    return bool(big) and sum(w[0].isupper() for w in big) / len(big) >= 0.6


def split_hymn_book(text):
    """A whole hymn book in one text: returns [(number, title, stanzas)].

    A hymn starts at "Hymn 23" / "No. 23" / "Orin 23" when the book writes them like that; otherwise at a line that
    is only a number, or a number and a title ("23. Amazing Grace"). Hymn numbers count up through the book while
    verse numbers start again in every hymn, so "2" where verse 2 is due is a verse, not hymn 2."""
    lines = (text or "").replace("\r\n", "\n").split("\n")
    explicit = sum(bool(EXPLICIT.match(line)) for line in lines) >= 3
    hymns, current, last = [], None, 0
    for index, line in enumerate(lines):
        start = (EXPLICIT if explicit else BARE).match(line)
        if start:
            number, rest = int(start[1]), start[2].strip()
            if current is not None and not explicit:
                due = current["verses"] + 1
                following = next((l.strip() for l in lines[index + 1:] if l.strip()), "")
                titled = _looks_like_title(rest) if rest else (_looks_like_title(following) and number == last + 1)
                if number == due and not titled:
                    current["verses"] = due  # a verse number inside this hymn
                    current["body"].append(f"{number}. {rest}" if rest else f"{number}.")
                    continue
            plausible = number > last and (not last or number <= last + 5)
            if plausible and (explicit or not rest or _looks_like_title(rest)):
                current = {"number": str(number), "title": rest.rstrip(".,;:"), "body": [], "verses": 1}
                hymns.append(current)
                last = number
                continue
        if current is not None:
            if not current["title"] and line.strip():
                current["title"] = re.sub(r"^\d+[.)]\s*", "", line.strip()).rstrip(".,;:!")
                if _looks_like_title(line.strip()):
                    continue  # a title line on its own; a first line used as the title stays in the words
            current["body"].append(line)
    out = []
    for hymn in hymns:
        stanzas = parse_lyrics("\n".join(hymn["body"]))
        if stanzas:
            out.append((hymn["number"], hymn["title"] or f"Hymn {hymn['number']}", stanzas))
    return out
