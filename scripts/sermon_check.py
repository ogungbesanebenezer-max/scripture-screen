"""Compare a SERMON TEST run (logs/app.log) with the sermon's own YouTube transcript: which verses the pastor
gave, which the app caught, how late, which it missed (with what it heard there) and what else it put up.

    python scripts/sermon_check.py                 the latest sermon test in logs/app.log
    python scripts/sermon_check.py --all           every sermon test in the log
    python scripts/sermon_check.py --log other.log --captions sermon.en.vtt

The transcript is YouTube's automatic captions (bench/sermons/<pastor>/<id>.en-orig.vtt, fetched for a pasted link
when missing). Captions make mistakes too, so a "missed" line can be the captions' fault: the report shows both
texts side by side. Only references the pastor says out loud are counted ("John 3 verse 16"); a verse quoted
without its reference is not, because the captions alone cannot show which verse it was.
The report is also saved as logs/sermon_check_<id>.txt.
"""
import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from verse_screen.bible import Library  # noqa: E402
from verse_screen.detector import Detector  # noqa: E402
from verse_screen.references import ReferenceFinder  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SERMONS = ROOT / "bench" / "sermons"
WINDOW, STEP = 8.0, 4.0  # caption words are read in 8-second pieces every 4 seconds, like the app's listening passes
EARLY, LATE = 15.0, 60.0  # the app's catch counts from 15 s before the pastor's words to 60 s after
LISTENED_NEAR = 20.0
MERGE_GAP = 12.0  # a verse counts only if the app was listening around that time (the operator may skip)

MARK = re.compile(r"\[sermon (\d+(?::\d\d){1,2})\] (Heard|On screen|Heard once, nothing against it)"
                  r"(?:: | -> )(.*)$")
LABEL = re.compile(r"^(.+?) (\d+):(\d+)(?:-(\d+))?$")
CUE_TIME = re.compile(r"^(\d+):(\d\d):(\d\d)\.(\d+) -->")
WORD_TIME = re.compile(r"<(\d+):(\d\d):(\d\d)\.(\d+)><c>([^<]*)</c>")
YOUTUBE_ID = re.compile(r"(?:v=|youtu\.be/|/live/|/shorts/|/embed/)([A-Za-z0-9_-]{11})")


def clock(text):
    seconds = 0
    for part in text.split(":"):
        seconds = seconds * 60 + int(part)
    return seconds


def fmt(seconds):
    seconds = int(max(0, seconds))
    hours, rest = divmod(seconds, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"


@dataclass
class Run:
    name: str
    source: str
    heard: list = field(default_factory=list)  # (time, text, [labels])
    shown: list = field(default_factory=list)  # (time, title)


def read_runs(path):
    """Every sermon test in the log, oldest first."""
    runs, current = [], None
    older = [path.with_name(f"{path.name}.{n}") for n in (3, 2, 1)]  # the log rolls over at 1 MB
    text = "".join(p.read_text(encoding="utf-8", errors="replace") for p in older + [path] if p.exists())
    for line in text.splitlines():
        if "Sermon test started: " in line:
            name, _, source = line.split("Sermon test started: ", 1)[1].rpartition(" | ")
            current = Run(name.strip(), source.strip())
            runs.append(current)
            continue
        if "Sermon test stopped" in line:
            current = None
            continue
        found = MARK.search(line) if current else None
        if not found:
            continue
        at, what, rest = clock(found.group(1)), found.group(2), found.group(3)
        if what == "On screen":
            current.shown.append((at, rest.strip()))
        elif what == "Heard":
            text, _, labels = rest.rpartition(" -> ")
            current.heard.append((at, text.strip(), [x.strip() for x in labels.split(",") if x.strip() != "-"]))
        else:  # heard once, put up after a quiet pass
            current.heard.append((at, "", [rest.strip()]))
    return runs


def caption_words(path):
    """[(seconds, word)] from YouTube's automatic captions, which time every word."""
    words, cue_start = [], None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        cue = CUE_TIME.match(line)
        if cue:
            h, m, s, frac = cue.groups()
            cue_start = int(h) * 3600 + int(m) * 60 + int(s) + float("0." + frac)
            continue
        if "<c>" not in line or cue_start is None:
            continue
        first = line.split("<", 1)[0].strip()
        if first:
            words.append((cue_start, first))
        for h, m, s, frac, word in WORD_TIME.findall(line):
            if word.strip():
                words.append((int(h) * 3600 + int(m) * 60 + int(s) + float("0." + frac), word.strip()))
    if not words:  # captions without word times: one entry per new line of text
        seen = set()
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            cue = CUE_TIME.match(line)
            if cue:
                h, m, s, frac = cue.groups()
                cue_start = int(h) * 3600 + int(m) * 60 + int(s) + float("0." + frac)
            elif cue_start is not None and line.strip() and "-->" not in line and line not in seen and \
                    not line.startswith(("WEBVTT", "Kind:", "Language:")):
                seen.add(line)
                words += [(cue_start, w) for w in re.sub(r"<[^>]+>", "", line).split()]
    return words


def text_between(words, start, end):
    return " ".join(w for t, w in words if start <= t < end)


def spoken_verses(words, library, finder):
    """[(seconds, [labels], caption text)] for the references in the captions, through the app's own detector."""
    detector = Detector(library, finder)
    found, end = [], words[-1][0] + WINDOW if words else 0
    at = WINDOW
    while at <= end:
        piece = text_between(words, at - WINDOW, at)
        events = detector.process(piece, at, 1.0) if piece else detector.flush(at)
        for item in events.found:
            if item.triggered or item.kind == "command":
                found.append((at, item.ref.label, piece))
        at += STEP
    return merge_pieces(found)


def _book_said(label, text):
    word = parse_label(label)[0].split()[-1].lower().rstrip("s")
    return word in text.lower()


def merge_pieces(found):
    """The 8-second cuts split references ("Judges 15 from verse" then "15:9-13"): one entry per passage said,
    timed when the pastor started saying it. Numbers read against the previous book are dropped."""
    found = [f for f in found if parse_label(f[1]) and (_book_said(f[1], f[2]) or not any(
        abs(g[0] - f[0]) <= MERGE_GAP and g is not f for g in found))]
    merged = []
    for at, label, piece in found:
        book, chapter = parse_label(label)[:2]
        last = merged[-1] if merged else None
        if last and at - last["end"] <= MERGE_GAP and parse_label(last["labels"][0])[:2] == (book, chapter):
            if label not in last["labels"]:
                last["labels"].append(label)
            last["end"], last["piece"] = at, piece
        else:
            merged.append({"at": at, "end": at, "labels": [label], "piece": piece})
    return [(m["at"], m["labels"], m["piece"]) for m in merged]


def parse_label(label):
    match = LABEL.match(label.strip())
    if not match:
        return None
    book, chapter, verse, last = match.groups()
    return book, int(chapter), int(verse), int(last or verse)


def same_passage(a, b):
    a, b = parse_label(a), parse_label(b)
    return bool(a and b and a[0] == b[0] and a[1] == b[1] and a[2] <= b[3] and b[2] <= a[3])


def find_captions(run, given=None):
    if given:
        return Path(given)
    source = run.source
    ids = [source] if re.fullmatch(r"[A-Za-z0-9_-]{11}", source) else YOUTUBE_ID.findall(source)
    for video_id in ids:
        for kind in ("en-orig", "en"):
            hits = sorted(SERMONS.glob(f"*/{video_id}.{kind}.vtt")) + sorted((ROOT / "logs" / "captions").glob(
                f"{video_id}.{kind}.vtt"))
            if hits:
                return hits[0]
    path = Path(source)
    if path.suffix and path.exists():
        beside = sorted(path.parent.glob(f"{path.stem}*.vtt"))
        if beside:
            return beside[0]
    if ids:  # a pasted link: fetch its captions once
        folder = ROOT / "logs" / "captions"
        folder.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, "-m", "yt_dlp", "-q", "--no-warnings", "--skip-download", "--write-auto-subs",
                        "--sub-langs", "en-orig,en", "--sub-format", "vtt", "-o", str(folder / "%(id)s.%(ext)s"),
                        f"https://www.youtube.com/watch?v={ids[0]}"], timeout=180, check=False)
        for kind in ("en-orig", "en"):
            if (folder / f"{ids[0]}.{kind}.vtt").exists():
                return folder / f"{ids[0]}.{kind}.vtt"
    return None


def read_key(path):
    """[(seconds, [labels], words)] from a checked answer key (<id>.key.tsv, made with build_key.py).
    Rows marked DROP are kept out: the hearers got them wrong."""
    spoken = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        cells = line.split("\t")
        if len(cells) < 3 or cells[1].strip().upper() == "DROP":
            continue
        spoken.append((clock(cells[0].strip()), [x.strip() for x in cells[2].split(" / ")],
                       cells[5] if len(cells) > 5 else ""))
    return spoken


def find_key(captions):
    if not captions:
        return None
    video_id = captions.name.split(".")[0]
    hits = sorted(SERMONS.glob(f"*/{video_id}.key.tsv"))
    return hits[0] if hits else None


def check(run, captions, library, finder, key=None):
    lines = [f"SERMON TEST: {run.name}", f"Source: {run.source}",
             f"Answer key: {key} (checked by hand)" if key else f"Transcript: {captions}", ""]
    if not run.heard:
        return lines + ["The app heard nothing in this test (was the sermon played?)."], None
    words = caption_words(captions) if captions and captions.exists() else []
    spoken = read_key(key) if key else spoken_verses(words, library, finder)
    heard_times = [t for t, _x, _l in run.heard]
    caught_by_app = [(t, label) for t, _x, labels in run.heard for label in labels]
    used = set()
    caught, missed, skipped = [], [], []
    for at, labels, piece in spoken:
        label = " / ".join(labels)
        if not any(abs(t - at) <= LISTENED_NEAR for t in heard_times):
            skipped.append((at, label))
            continue
        hit = next(((t, l) for t, l in caught_by_app if at - EARLY <= t <= at + LATE and
                    any(same_passage(l, x) for x in labels)), None)
        if hit:
            caught.append((at, label, hit[0] - at, hit[1]))
            used.update((t, l) for t, l in caught_by_app if any(same_passage(l, x) for x in labels) and
                        abs(t - hit[0]) <= LATE)
        else:
            heard_there = " | ".join(x for t, x, _l in run.heard if at - WINDOW <= t <= at + 2 * WINDOW and x)
            missed.append((at, label, piece, heard_there))
    extra, seen = [], set()
    for t, label in caught_by_app:
        if (t, label) in used or label in seen:
            continue
        if not any(same_passage(label, x) and abs(s_at - t) <= LATE + EARLY for s_at, xs, _p in spoken for x in xs):
            seen.add(label)
            said = text_between(words, t - WINDOW - 4, t + 2)
            app_said = next((x for ht, x, ls in run.heard if ht == t and label in ls), "")
            up = any(re.sub(r" \(.*\)$", "", title) == label and abs(st - t) <= 5 for st, title in run.shown)
            extra.append((t, label, app_said, said, up))
    counted = len(caught) + len(missed)
    lags = sorted(lag for _a, _l, lag, _h in caught)
    lines.append(f"Listened from {fmt(min(heard_times))} to {fmt(max(heard_times))}; "
                 f"{len(run.shown)} verses went on screen.")
    if counted:
        median = lags[len(lags) // 2] if lags else 0
        lines.append(f"References the pastor said ({'answer key' if key else 'per the captions'}): {counted}.  Caught {len(caught)} "
                     f"({len(caught) / counted:.0%}), missed {len(missed)}"
                     + (f", median {median:+.0f} s after the words, slowest {lags[-1]:+.0f} s." if lags else "."))
    else:
        lines.append("The captions have no spoken references in the part the app listened to.")
    if skipped:
        lines.append(f"Not counted (the test skipped past them): {len(skipped)}.")
    lines.append(f"Found by the app but not given as a reference in the captions: {len(extra)}, of which "
                 f"{sum(1 for x in extra if x[4])} went on screen.")
    lines += ["", "CAUGHT  (sermon time, verse, seconds after the pastor said it)"]
    lines += [f"  {fmt(at):>8}  {label:28} {lag:+5.0f} s" + (f"   as {got}" if got not in label.split(" / ") else "")
              for at, label, lag, got in caught] or ["  none"]
    lines += ["", "MISSED  (what the captions say / what the app heard there)"]
    for at, label, piece, heard_there in missed:
        lines += [f"  {fmt(at):>8}  {label}", f"            captions: {piece[-160:]}",
                  f"            app heard: {heard_there[:220] or '(nothing)'}"]
    if not missed:
        lines.append("  none")
    lines += ["", "EXTRA  (the app found these; the captions do not give the reference there. ON SCREEN = the "
              "congregation saw it; list = only in the operator's list. Many are right: verses quoted without "
              "their reference)"]
    for t, label, app_said, said, up in extra:
        lines += [f"  {fmt(t):>8}  {label:28} {'ON SCREEN' if up else 'list'}",
                  f"            app heard: {app_said[:160] or '(put up after a quiet pass)'}",
                  f"            captions: {said[-160:]}"]
    if not extra:
        lines.append("  none")
    return lines, {"caught": len(caught), "missed": len(missed), "extra": len(extra),
                   "extra_on_screen": sum(1 for x in extra if x[4]), "lags": lags}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--log", default=str(ROOT / "logs" / "app.log"))
    parser.add_argument("--captions", help="the sermon's .vtt transcript, when it cannot be found by itself")
    parser.add_argument("--all", action="store_true", help="every sermon test in the log, not just the latest")
    args = parser.parse_args()
    runs = read_runs(Path(args.log))
    if not runs:
        print(f"No sermon test in {args.log}. Open SERMON TEST on the Bible page, play a sermon, then run this.")
        return 1
    library = Library()
    finder = ReferenceFinder(library)
    for run in runs if args.all else runs[-1:]:
        captions = find_captions(run, args.captions)
        key = find_key(captions)
        if not key and (not captions or not captions.exists()):
            print(f"{run.name}: no transcript found. Pass one with --captions file.vtt\n")
            continue
        lines, _totals = check(run, captions, library, finder, key)
        report = "\n".join(lines)
        print(report + "\n")
        name = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(run.source).stem if Path(run.source).suffix else run.source)
        out = ROOT / "logs" / f"sermon_check_{name[-40:] or 'run'}.txt"
        out.write_text(report + "\n", encoding="utf-8")
        print(f"(saved to {out})\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
