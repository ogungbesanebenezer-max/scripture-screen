"""Build data/hymns/public_domain.json from the Open Hymnal Project (openhymnal.org, mirror github.com/mzealey/openhymnal).

    python scripts/import_open_hymnal.py path/to/openhymnal/Complete

Only scores marked "copyright: public domain" are taken. The words are under the notes in ABC ("w:" lines, one row
per verse in each block, sung syllables split with "-"); the hymn's metre ("8 7 8 7" syllables a line) puts the
line breaks back. Extra verses printed under the score ("W:" lines) already have their lines.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "data" / "hymns" / "public_domain.json"
LABEL = re.compile(r"^\s*(\d{1,2})\.\s*~?\s*")


def syllables(text):
    """The sung pieces of one w: row: 'A- bide with me;' -> ['A-', 'bide', 'with', 'me;']."""
    text = text.replace("\\-", "\x00").replace("|", " ")
    pieces = []
    for word in text.split():
        parts = re.split(r"(?<=-)", word)  # 'ev-en-tide' -> 'ev-', 'en-', 'tide'
        pieces += [p for p in parts if p not in ("", "-")]
    return [p.replace("\x00", "-") for p in pieces if p not in ("*", "_")]


def join(pieces):
    words, out = "", []
    for piece in pieces:
        piece = piece.replace("_", "")
        if piece.endswith("-"):
            words += piece[:-1]
        else:
            out.append((words + piece).replace("~", " "))
            words = ""
    if words:
        out.append(words)
    return " ".join(out)


def lines_by_metre(pieces, metre):
    """Break the sung pieces into poem lines using the metre's syllables per line."""
    counts = [int(n) for n in re.findall(r"\d+", metre or "")]
    total = sum(counts)
    if not counts or len(pieces) % total:
        return None
    lines, at = [], 0
    while at < len(pieces):
        for count in counts:
            lines.append(join(pieces[at:at + count]).strip())
            at += count
    return lines


def lines_by_punctuation(text, longest=42):
    """No usable metre: break at ; : . ! ? and then at the comma nearest the middle of any line still too long."""
    out = []
    for part in (p.strip() for p in re.split(r"(?<=[;:.!?])\s+", text) if p.strip()):
        pending = [part]
        while pending:
            line = pending.pop(0)
            commas = [m.end() for m in re.finditer(r",\s", line)]
            if len(line) <= longest or not commas:
                out.append(line)
                continue
            cut = min(commas, key=lambda at: abs(at - len(line) / 2))
            pending[:0] = [line[:cut].strip(), line[cut:].strip()]
    return out


def tidy(line):
    line = re.sub(r"\s+([,;:.!?])", r"\1", line)
    line = re.sub(r"([,;:!?])(?=[A-Za-z])", r"\1 ", line)
    return line[:1].upper() + line[1:]


def parse_abc(text):
    fields = {}
    for key in ("OHAUTHOR", "OHMETRICAL", "OHTRANSLATOR"):
        match = re.search(rf"^%{key}\s+(.*)$", text, re.M)
        fields[key] = match[1].strip() if match else ""
    title = re.search(r"^T:\s*(.+)$", text, re.M)[1].strip()
    rights = " ".join(re.findall(r"^C:\s*copyright:\s*(.*)$", text, re.M)).lower()
    blocks, current, lines = [], [], text.splitlines()
    for line in lines:
        if line.startswith("w:"):
            current.append(line[2:].strip())
        elif current and not line.startswith("%"):
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    rows = max((len(b) for b in blocks), default=0)
    verses = [[] for _ in range(rows)]
    refrain = []
    for block in blocks:
        if len(block) < rows:  # fewer rows than verses: the refrain, sung the same after every verse
            refrain += syllables(block[0])
            continue
        for index, row in enumerate(block):
            verses[index] += syllables(row)
    stanzas = []
    for pieces in verses:
        if not pieces:
            continue
        first = LABEL.match(pieces[0])
        if first:
            pieces[0] = pieces[0][first.end():]
            if not pieces[0]:
                pieces = pieces[1:]
        stanzas.append(pieces)
    if refrain and len(stanzas) > 1:
        # "It is" sung into the refrain shows up at the end of verse 1's row only (the others hold rests there)
        common = min(len(p) for p in stanzas[1:])
        if len(stanzas[0]) > common:
            refrain = stanzas[0][common:] + refrain
            stanzas[0] = stanzas[0][:common]
    counts = [int(n) for n in re.findall(r"\d+", fields["OHMETRICAL"])]
    split = next((k for k in range(1, len(counts) + 1) if stanzas and sum(counts[:k]) == len(stanzas[0])), None)
    verse_metre = " ".join(map(str, counts[:split])) if split else fields["OHMETRICAL"]
    refrain_metre = " ".join(map(str, counts[split:])) if split else ""
    out = []
    for pieces in stanzas:
        broken = lines_by_metre(pieces, verse_metre) or lines_by_punctuation(join(pieces))
        out.append({"label": f"Verse {len(out) + 1}", "text": "\n".join(tidy(l) for l in broken if l)})
    if refrain:
        broken = lines_by_metre(refrain, refrain_metre) or lines_by_punctuation(join(refrain))
        out.append({"label": "Chorus", "text": "\n".join(tidy(l) for l in broken if l)})
    extra = "\n".join(line[2:].rstrip() for line in lines if line.startswith("W:"))
    for block in re.split(r"\n\s*\n", extra):
        body = [l.strip() for l in block.strip().splitlines() if l.strip()]
        if not body:
            continue
        body[0] = LABEL.sub("", body[0])
        out.append({"label": f"Verse {sum(1 for s in out if s['label'] != 'Chorus') + 1}", "text": "\n".join(body)})
    return {"title": title, "author": fields["OHAUTHOR"], "translator": fields["OHTRANSLATOR"],
            "public_domain": rights.startswith("public domain"), "stanzas": out}


def main(folder):
    hymns, seen = [], set()
    for path in sorted(Path(folder).glob("*/*.abc")):
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")  # some of the older scores
        hymn = parse_abc(text)
        key = re.sub(r"[^a-z]", "", hymn["title"].lower())
        if not hymn["public_domain"] or not hymn["stanzas"] or key in seen:
            continue
        seen.add(key)
        hymn.pop("public_domain")
        hymns.append(hymn)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    note = ("Public-domain hymn words from the Open Hymnal Project (openhymnal.org, Brian J. Dumont), taken only "
            "where the score says 'copyright: public domain'. Built by scripts/import_open_hymnal.py.")
    OUTPUT.write_text(json.dumps({"source": note, "hymns": hymns}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"saved {len(hymns)} hymns to {OUTPUT}")


if __name__ == "__main__":
    main(sys.argv[1])
