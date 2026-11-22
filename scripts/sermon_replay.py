"""Replay SERMON TEST runs through the current detector in seconds, and score them against a frozen answer list.

A real sermon test takes as long as the sermon. This takes the sentences the app heard in a run (logs/app.log),
feeds them to today's detector at the same moments, applies the screen's own rule (a confident find goes live;
"verse 1" completed by "verse 18" swaps in), and compares what would have gone up with what the pastor said.

    python scripts/sermon_replay.py --freeze            write the answer lists (once, before changing detection)
    python scripts/sermon_replay.py                     score every run in the log against them
    python scripts/sermon_replay.py --show              also list every verse that would go on screen

The answers are frozen because sermon_check reads the captions with the detector itself: changing the detector
would otherwise change the answers too. They live in logs/replay_answers/<id>.json; a <id>.fix.json beside one
corrects the captions where they misheard (listed in the report as "captions wrong").
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sermon_check as sc  # noqa: E402
from verse_screen.bible import Library  # noqa: E402
from verse_screen.detector import Detector  # noqa: E402
from verse_screen.references import ReferenceFinder  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ANSWERS = ROOT / "logs" / "replay_answers"
COMPLETE_SECONDS, PASS_SECONDS = 15, 1.6
LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3}) INFO \[sermon (\d+(?::\d\d){1,2})\] Heard: (.*) -> .*$")


def read_runs(path):
    """[(name, source, [(wall seconds, sermon seconds, text)])] for every sermon test in the log."""
    runs, current = [], None
    older = [path.with_name(f"{path.name}.{n}") for n in (3, 2, 1)]
    text = "".join(p.read_text(encoding="utf-8", errors="replace") for p in older + [path] if p.exists())
    for line in text.splitlines():
        if "Sermon test started: " in line:
            name, _, source = line.split("Sermon test started: ", 1)[1].rpartition(" | ")
            current = (name.strip(), source.strip(), [])
            runs.append(current)
        elif "Sermon test stopped" in line:
            current = None
        elif current and (found := LINE.match(line)):
            wall = datetime.strptime(found[1], "%Y-%m-%d %H:%M:%S,%f").timestamp()
            current[2].append((wall, sc.clock(found[2]), found[3].strip()))
    return [r for r in runs if len(r[2]) > 30]


def replay(lines, library, finder):
    """What would go on screen: [(sermon seconds, label)], and everything found: [(sermon seconds, label)]."""
    detector = Detector(library, finder)
    live, live_at, shown, found_all = None, -1e9, [], []
    start_wall, start_sermon = lines[0][0], lines[0][1]

    def sermon_time(wall):
        return start_sermon + (wall - start_wall)

    def apply(found, wall):
        nonlocal live, live_at
        found_all.append((sermon_time(wall), found.ref.label))
        grows = bool(live) and wall - live_at <= COMPLETE_SECONDS and (found.ref.book, found.ref.chapter) == (
            live.book, live.chapter) and found.ref.verse != live.verse and str(found.ref.verse).startswith(
            str(live.verse))
        if not (found.triggered or grows):
            return
        ref = found.ref
        if ref.end_verse and ref.end_verse > ref.verse:  # the app shows a passage one verse at a time
            ref = type(ref)(ref.book, ref.chapter, ref.verse)
        if not library.text("KJV", ref):
            return
        live, live_at = ref, wall
        shown.append((sermon_time(wall), ref.label))
        detector.set_context(ref)
        detector.note_shown(ref, found.translation or "KJV")

    previous = None
    for wall, _sermon, text in lines:
        if previous is not None:  # the screen's 80 ms tick: a clear verse heard once goes up after a quiet pass
            tick = previous + 0.08
            while tick < wall:
                for found in detector.flush(tick).found:
                    apply(found, tick)
                tick += 0.4
        for found in detector.process(text, wall, PASS_SECONDS).found:
            apply(found, wall)
        previous = wall
    return shown, found_all


def answers_path(source):
    return ANSWERS / f"{source}.json"


def freeze(runs, library, finder):
    ANSWERS.mkdir(parents=True, exist_ok=True)
    for name, source, _lines in runs:
        captions = sc.find_captions(type("R", (), {"source": source})())
        spoken = sc.spoken_verses(sc.caption_words(captions), library, finder)
        answers_path(source).write_text(json.dumps([[at, labels, piece] for at, labels, piece in spoken], indent=1))
        print(f"froze {len(spoken)} references for {name}")


def score(name, source, lines, shown, found_all, show=False):
    answers = json.loads(answers_path(source).read_text())
    fix = answers_path(source).with_suffix(".fix.json")
    fixes = json.loads(fix.read_text()) if fix.exists() else {}
    first, last = lines[0][1], lines[-1][1]
    counted = [(at, labels) for at, labels, _p in answers if first - 5 <= at <= last - 10]
    caught, missed, used = [], [], set()
    for at, labels in counted:
        labels = fixes.get(" / ".join(labels), labels)
        hit = next(((t, l) for t, l in shown if at - sc.EARLY <= t <= at + sc.LATE and
                    any(sc.same_passage(l, x) for x in labels)), None)
        if hit:
            caught.append((at, labels, hit[0] - at))
            used.update((t, l) for t, l in shown if any(sc.same_passage(l, x) for x in labels) and
                        at - sc.EARLY <= t <= at + sc.LATE)
        else:
            missed.append((at, labels))
    wrong = [(t, l) for t, l in shown if (t, l) not in used]
    lags = sorted(lag for _a, _l, lag in caught)
    print(f"\n{name[:60]}\n  said {len(counted)}, on screen in time {len(caught)}, missed {len(missed)}, "
          f"other things on screen {len(wrong)}" + (f", delay median {lags[len(lags) // 2]:+.0f} s, "
                                                    f"slowest {lags[-1]:+.0f} s" if lags else ""))
    for at, labels in missed:
        print(f"    MISSED  {sc.fmt(at):>6}  {' / '.join(labels)}")
    for t, label in wrong:
        print(f"    OTHER   {sc.fmt(t):>6}  {label}")
    if show:
        for t, label in shown:
            print(f"    shown   {sc.fmt(t):>6}  {label}")
    return len(counted), len(caught), len(wrong)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--log", default=str(ROOT / "logs" / "app.log"))
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()
    library = Library()
    finder = ReferenceFinder(library)
    runs = read_runs(Path(args.log))
    if args.freeze:
        freeze(runs, library, finder)
        return 0
    total = [0, 0, 0]
    for name, source, lines in runs:
        if not answers_path(source).exists():
            print(f"{name}: no frozen answers; run with --freeze first")
            continue
        shown, found_all = replay(lines, library, finder)
        for i, n in enumerate(score(name, source, lines, shown, found_all, args.show)):
            total[i] += n
    print(f"\nTOTAL: said {total[0]}, on screen in time {total[1]}, other things on screen {total[2]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
