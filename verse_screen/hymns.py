"""Hymn lyrics: typed or pasted in, or imported from text files and OpenLyrics (.xml, exported by OpenLP and
most worship software). Shown one stanza at a time, with the chorus after each verse unless the hymn sets its own
order."""
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

CHORUS_WORDS = r"(chorus|refrain|ref\.?|egbe|ègbè)"  # Egbe: chorus in Yoruba hymn books
CHORUS_LINE = re.compile(rf"^\s*\[?{CHORUS_WORDS}\]?\s*:?\s*$", re.I)
CHORUS_START = re.compile(rf"^\s*\[?{CHORUS_WORDS}\]?\s*[:.]\s*", re.I)
VERSE_LINE = re.compile(r"^\s*\[?(verse|v\.?|stanza|ese)?\s*(\d{1,2})\]?\s*[:.)]?\s*$", re.I)
VERSE_START = re.compile(r"^\s*(\d{1,2})[.)]\s+")
HEADER = re.compile(r"^#\s*(?:(\d+[a-z]?)\s*[.:-]?\s+)?(.+?)\s*$", re.I)


@dataclass
class Stanza:
    label: str  # "Verse 1", "Chorus"
    text: str

    @property
    def is_chorus(self):
        return self.label.lower().startswith(("chorus", "refrain"))


@dataclass
class Hymn:
    id: int
    title: str
    number: str = ""
    collection: str = ""
    ordered: bool = False  # True: show the stanzas exactly as stored (an imported verse order)
    stanzas: list = field(default_factory=list)

    @property
    def label(self):
        return f"{self.number}. {self.title}" if self.number else self.title

    def first_line(self):
        return self.stanzas[0].text.split("\n")[0] if self.stanzas else ""


def parse_lyrics(text):
    """Stanzas are separated by a blank line. A stanza that starts with "Chorus" (or "Refrain", "Egbe") is the
    chorus; "1." or "Verse 1" at the start is taken as the verse label and left off the screen."""
    stanzas, verse = [], 0
    blocks = [b for b in re.split(r"\n\s*\n", (text or "").replace("\r\n", "\n").strip()) if b.strip()]
    for block in blocks:
        lines = [line.rstrip() for line in block.strip().split("\n")]
        label = None
        if CHORUS_LINE.match(lines[0]):
            label, lines = "Chorus", lines[1:]
        elif CHORUS_START.match(lines[0]):
            label, lines[0] = "Chorus", CHORUS_START.sub("", lines[0])
        elif VERSE_LINE.match(lines[0]):
            verse, lines = int(VERSE_LINE.match(lines[0])[2]), lines[1:]
            label = f"Verse {verse}"
        elif VERSE_START.match(lines[0]):
            verse = int(VERSE_START.match(lines[0])[1])
            label, lines[0] = f"Verse {verse}", VERSE_START.sub("", lines[0])
        body = "\n".join(line.strip() for line in lines if line.strip())
        if not body:
            continue
        if label is None:
            verse += 1
            label = f"Verse {verse}"
        stanzas.append(Stanza(label, body))
    return stanzas


def lyrics_text(stanzas):
    """Back to the editable form parse_lyrics reads."""
    parts = []
    for stanza in stanzas:
        parts.append(("Chorus:\n" if stanza.is_chorus else "") + stanza.text)
    return "\n\n".join(parts)


def sequence(hymn, repeat_chorus=True):
    """The order the stanzas go on screen: each verse followed by the chorus, as congregations sing them."""
    stanzas = list(hymn.stanzas)
    if hymn.ordered or not repeat_chorus:
        return stanzas
    choruses = [s for s in stanzas if s.is_chorus]
    verses = [s for s in stanzas if s not in choruses]
    if len(choruses) != 1 or not verses:
        return stanzas
    chorus = choruses[0]
    if stanzas[0] is chorus:  # a hymn that opens with its chorus keeps it first too
        out = [chorus]
    else:
        out = []
    for verse in verses:
        out += [verse, chorus]
    return out


def parse_text_file(text, default_title):
    """One hymn per file, or several, each starting with a header line: "# 23 Amazing Grace"."""
    text = (text or "").replace("\r\n", "\n").lstrip("﻿")
    if not any(HEADER.match(line) for line in text.split("\n")):
        return [("", default_title, parse_lyrics(text))] if text.strip() else []
    hymns, current = [], None
    for line in text.split("\n"):
        header = HEADER.match(line)
        if header:
            current = [header[1] or "", header[2].strip(), []]
            hymns.append(current)
        elif current is not None:
            current[2].append(line)
    return [(number, title, parse_lyrics("\n".join(lines))) for number, title, lines in hymns
            if title and "".join(lines).strip()]


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _lines_text(lines):
    """OpenLyrics <lines>: text with <br/> line breaks and optional <chord>/<comment> tags."""
    out = [lines.text or ""]
    for child in lines:
        name = _local(child.tag)
        if name == "br":
            out.append("\n")
        elif name not in ("comment",):
            out.append("".join(child.itertext()))
        out.append(child.tail or "")
    return "\n".join(line.strip() for line in "".join(out).split("\n") if line.strip())


def parse_openlyrics(data):
    """Returns (number, title, collection, stanzas, ordered)."""
    root = ET.fromstring(data)
    props = next((c for c in root if _local(c.tag) == "properties"), None)
    title, number, collection, order = "", "", "", ""
    if props is not None:
        for child in props.iter():
            name = _local(child.tag)
            if name == "title" and not title:
                title = (child.text or "").strip()
            elif name == "songbook" and not collection:
                collection, number = child.get("name", ""), child.get("entry", "")
            elif name == "verseOrder":
                order = (child.text or "").split()
    named = {}
    for verse in root.iter():
        if _local(verse.tag) != "verse":
            continue
        key = verse.get("name", f"v{len(named) + 1}")
        text = "\n".join(_lines_text(lines) for lines in verse if _local(lines.tag) == "lines")
        if text:
            named[key] = text

    def label(key):
        kind, digits = key[:1].lower(), "".join(ch for ch in key if ch.isdigit())
        word = {"v": "Verse", "c": "Chorus", "b": "Bridge", "p": "Pre-chorus", "e": "Ending"}.get(kind, "Verse")
        return f"{word} {digits}".strip() if word != "Chorus" or digits not in ("", "1") else "Chorus"

    keys = [k for k in order if k in named] or list(named)
    stanzas = [Stanza(label(k), named[k]) for k in keys]
    return number, title or "Untitled hymn", collection, stanzas, bool(order)
