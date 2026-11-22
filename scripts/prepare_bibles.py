"""One-off: convert raw scrollmapper Bible JSON into this project's format.

Usage: python scripts/prepare_bibles.py RAW_DIR OUT_DIR
The output is already committed in data/bibles, so normal users never run this.
"""
import json
import re
import sys
from pathlib import Path

NAMES = {
    "KJV": ("KJV", "King James Version"),
    "ASV": ("ASV", "American Standard Version"),
    "BBE": ("BBE", "Bible in Basic English"),
    "YLT": ("YLT", "Young's Literal Translation"),
}
FIX = {"Psalm": "Psalms", "Revelation of John": "Revelation", "Song of Songs": "Song of Solomon",
       "Canticles": "Song of Solomon"}
ROMAN = {"I": "1", "II": "2", "III": "3"}


def book_name(raw):
    first, _, rest = raw.partition(" ")
    name = f"{ROMAN[first]} {rest}" if first in ROMAN and rest else raw
    return FIX.get(name, name)


def clean(text):
    text = text.replace("¶", " ").replace("‹", "").replace("›", "")
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def main(raw_dir, out_dir):
    for raw_name, (code, title) in NAMES.items():
        raw = json.loads((Path(raw_dir) / f"{raw_name}.json").read_text(encoding="utf-8-sig"))
        books, count = {}, 0
        for book in raw["books"]:
            chapters = {}
            for chapter in book["chapters"]:
                verses = {str(v["verse"]): clean(v["text"]) for v in chapter["verses"] if clean(v["text"])}
                chapters[str(chapter["chapter"])] = verses
                count += len(verses)
            books[book_name(book["name"])] = chapters
        output = {"code": code, "name": title, "books": books}
        (Path(out_dir) / f"{code}.json").write_text(
            json.dumps(output, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(code, len(books), "books", count, "verses")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
