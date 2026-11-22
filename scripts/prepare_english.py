"""One-off: add two modern-English Bibles that are in the public domain, so no licence is needed.

    python scripts/prepare_english.py bsb.txt engwebp_vpl.txt data/bibles

BSB = Berean Standard Bible (https://bereanbible.com/bsb.txt), WEB = World English Bible, Protestant edition
(https://ebible.org/Scriptures/engwebp_vpl.zip). The only change to the text is the file format.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.prepare_yoruba import CODES, NAMES  # noqa: E402
from verse_screen.bible_names import BOOK_SET  # noqa: E402

BSB_LINE = re.compile(r"^(.+?) (\d+):(\d+)\t(.*)$")
WEB_LINE = re.compile(r"^(\S{3}) (\d+):(\d+) (.*)$")
BSB_NAMES = {"Psalm": "Psalms", "Song of Songs": "Song of Solomon"}


def tidy(text):
    return re.sub(r"\s+", " ", text).strip()


def save(code, name, books, folder):
    unknown = set(books) - BOOK_SET
    if unknown or len(books) != 66:
        sys.exit(f"{code}: {len(books)} books, unknown names {sorted(unknown)}")
    count = sum(len(verses) for chapters in books.values() for verses in chapters.values())
    result = {"code": code, "name": name, "books": books}
    (Path(folder) / f"{code}.json").write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                                               encoding="utf-8")
    print(f"{code}: {len(books)} books, {count} verses")


def main(bsb, web, folder):
    books = {}
    for line in Path(bsb).read_text(encoding="utf-8-sig").splitlines():
        match = BSB_LINE.match(line)
        if match and tidy(match[4]):
            book = BSB_NAMES.get(match[1], match[1])
            books.setdefault(book, {}).setdefault(match[2], {})[match[3]] = tidy(match[4])
    save("BSB", "Berean Standard Bible", books, folder)
    book_of, books = dict(zip(CODES, NAMES, strict=True)), {}
    for line in Path(web).read_text(encoding="utf-8-sig").splitlines():
        match = WEB_LINE.match(line)
        if match and tidy(match[4]):
            books.setdefault(book_of[match[1]], {}).setdefault(match[2], {})[match[3]] = tidy(match[4])
    # WEB prints the closing blessing of Romans after chapter 14; the other Bibles here number it 16:25-27.
    for old, new in (("24", "25"), ("25", "26"), ("26", "27")):
        books["Romans"]["16"].setdefault(new, books["Romans"]["14"].pop(old))
    save("WEB", "World English Bible", books, folder)


if __name__ == "__main__":
    main(*sys.argv[1:4])
