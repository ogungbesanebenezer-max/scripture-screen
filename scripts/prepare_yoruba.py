"""One-off: convert eBible.org's yor_vpl.txt (Open Yoruba Contemporary Bible, CC BY-SA 4.0) into this project's format.

Usage: python scripts/prepare_yoruba.py yor_vpl.txt data/bibles/YOR.json
The only change to the text is the file format. Credit and licence are in data/bibles/NOTICE.txt.
"""
import json
import re
import sys
from pathlib import Path

CODES = ("GEN EXO LEV NUM DEU JOS JDG RUT 1SA 2SA 1KI 2KI 1CH 2CH EZR NEH EST JOB PSA PRO ECC SOL ISA JER LAM EZE DAN HOS "
         "JOE AMO OBA JON MIC NAH HAB ZEP HAG ZEC MAL MAT MAR LUK JOH ACT ROM 1CO 2CO GAL EPH PHI COL 1TH 2TH 1TI 2TI TIT "
         "PHM HEB JAM 1PE 2PE 1JO 2JO 3JO JUD REV").split()
NAMES = ["Genesis", "Exodus", "Leviticus", "Numbers", "Deuteronomy", "Joshua", "Judges", "Ruth", "1 Samuel", "2 Samuel",
         "1 Kings", "2 Kings", "1 Chronicles", "2 Chronicles", "Ezra", "Nehemiah", "Esther", "Job", "Psalms", "Proverbs",
         "Ecclesiastes", "Song of Solomon", "Isaiah", "Jeremiah", "Lamentations", "Ezekiel", "Daniel", "Hosea", "Joel",
         "Amos", "Obadiah", "Jonah", "Micah", "Nahum", "Habakkuk", "Zephaniah", "Haggai", "Zechariah", "Malachi", "Matthew",
         "Mark", "Luke", "John", "Acts", "Romans", "1 Corinthians", "2 Corinthians", "Galatians", "Ephesians", "Philippians",
         "Colossians", "1 Thessalonians", "2 Thessalonians", "1 Timothy", "2 Timothy", "Titus", "Philemon", "Hebrews", "James",
         "1 Peter", "2 Peter", "1 John", "2 John", "3 John", "Jude", "Revelation"]
LINE = re.compile(r"^(\S{3}) (\d+):(\d+) (.*)$")


def main(source, output):
    book_of = dict(zip(CODES, NAMES, strict=True))
    books, count = {}, 0
    for line in Path(source).read_text(encoding="utf-8-sig").splitlines():
        match = LINE.match(line)
        if not match:
            continue
        code, chapter, verse, text = match.groups()
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            books.setdefault(book_of[code], {}).setdefault(chapter, {})[verse] = text
            count += 1
    result = {"code": "YOR", "name": "Yoruba Contemporary Bible (open edition)", "books": books}
    Path(output).write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"YOR: {len(books)} books, {count} verses")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
