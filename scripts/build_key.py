"""Build a checked answer key for a test sermon: every verse the pastor names, confirmed against the Bible text.

    python scripts/build_key.py <video id>        writes bench/sermons/*/<id>.key.draft.tsv for review

Two hearers: YouTube's captions and Parakeet (<id>.parakeet.tsv, from bench/keys/hear.py). Every reference
either one hears is a candidate. A candidate is READ when the verse's own words follow within ~2.5 minutes in
what Parakeet heard (the pastor or the reader read it out); otherwise NAMED, to be checked by hand. The reviewed
file is saved as <id>.key.tsv; sermon_check.py uses it in place of the captions.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sermon_check as sc  # noqa: E402
from verse_screen.bible import Library, Ref  # noqa: E402
from verse_screen.references import ReferenceFinder  # noqa: E402

STOP = set("the and of to in that a is he for unto i it be with his they shall not was all them you thou thy "
           "my me which but as by have him are from there were this so ye on their at when what or who will "
           "then into hath an we our your us if do no".split())


def parakeet_words(path):
    rows = [line.split("\t", 1) for line in path.read_text(encoding="utf-8").splitlines() if "\t" in line]
    words = []
    for i, (start, text) in enumerate(rows):
        start = float(start)
        end = float(rows[i + 1][0]) if i + 1 < len(rows) else start + 15
        parts = text.split()
        words += [(start + (end - start) * k / max(1, len(parts)), w) for k, w in enumerate(parts)]
    return words


def content(text):
    return [w for w in re.findall(r"[a-z']+", text.lower()) if w not in STOP and len(w) > 2]


def read_score(library, label, words, at):
    """Share of the verse's own words heard within a 40-word stretch after it was named (best stretch)."""
    parsed = sc.parse_label(label)
    if not parsed:
        return 0.0
    book = "Psalms" if parsed[0] == "Psalm" else parsed[0]
    best = 0.0
    for code in ("KJV", "NKJV"):
        if code not in library.codes:
            continue
        text = library.text(code, Ref(book, parsed[1], parsed[2]))
        wanted = set(content(text or ""))
        if len(wanted) < 3:
            continue
        heard = [w for w in content(sc.text_between(words, at - 20, at + 150))]
        for i in range(0, max(1, len(heard) - 10), 3):
            got = wanted & set(heard[i:i + 40])
            best = max(best, len(got) / len(wanted))
    return best


def main():
    video_id = sys.argv[1]
    audio = next(sc.SERMONS.glob(f"*/{video_id}.opus"))
    library = Library()
    finder = ReferenceFinder(library)
    hearers = {"parakeet": parakeet_words(audio.with_suffix(".parakeet.tsv"))}
    vtt = audio.with_name(f"{video_id}.en-orig.vtt")
    if vtt.exists():
        hearers["captions"] = sc.caption_words(vtt)
    found = []  # (at, labels, who)
    for who, words in hearers.items():
        found += [(at, labels, who) for at, labels, _p in sc.spoken_verses(words, library, finder)]
    found.sort()
    groups = []
    for at, labels, who in found:
        chapters = {sc.parse_label(x)[:2] for x in labels}
        group = next((g for g in groups if abs(g["at"] - at) <= 30 and chapters & g["chapters"]), None)
        if group:
            group["labels"].update(labels)
            group["who"].add(who)
            group["at"] = min(group["at"], at)
        else:
            groups.append({"at": at, "labels": set(labels), "chapters": chapters, "who": {who}})
    words = hearers["parakeet"]
    out = audio.with_name(f"{video_id}.key.draft.tsv")
    lines = ["# time\tstatus\treference\tscore\theard by\twhat Parakeet heard"]
    for g in sorted(groups, key=lambda g: g["at"]):
        scored = sorted(((read_score(library, x, words, g["at"]), x) for x in g["labels"]), reverse=True)
        score, best = scored[0]
        ranged = [x for s, x in scored if sc.parse_label(x)[2:] == sc.parse_label(best)[2:] or s >= score - 0.05]
        label = max(ranged, key=lambda x: (sc.parse_label(x)[3] - sc.parse_label(x)[2], x))  # the full range said
        status = "READ" if score >= 0.5 else "NAMED"
        others = ", ".join(sorted(g["labels"] - {label}))
        lines.append(f"{sc.fmt(g['at'])}\t{status}\t{label}\t{score:.2f}\t{'+'.join(sorted(g['who']))}"
                     f"{'  (also: ' + others + ')' if others else ''}\t"
                     f"{sc.text_between(words, g['at'] - 10, g['at'] + 6)[-200:]}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\n{len(groups)} candidates -> {out}")


if __name__ == "__main__":
    main()
