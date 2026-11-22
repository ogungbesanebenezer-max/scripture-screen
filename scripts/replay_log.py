"""Replay every sentence the app heard (logs/app.log) through the current code and compare with what went up then.

    python scripts/replay_log.py [path/to/app.log] [seconds per pass, default 4.6]

Run it after every change: a line under CHANGED that is not just "caught a few seconds earlier" means a regression.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from verse_screen.bible import Library  # noqa: E402
from verse_screen.detector import Detector  # noqa: E402
from verse_screen.references import ReferenceFinder  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "logs" / "app.log"
    speed = float(sys.argv[2]) if len(sys.argv) > 2 else 4.6
    library = Library()
    finder = ReferenceFinder(library)
    detector, previous, shown_recently = None, None, {}
    same = new = changed = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if " Heard: " not in line or " -> " not in line:
            continue
        day, clock = line[:10], line[11:19]
        h, m, s = map(int, clock.split(":"))
        now = h * 3600 + m * 60 + s
        if previous is None or previous[0] != day or now - previous[1] > 300:
            detector, shown_recently = Detector(library, finder), {}  # a new session
        elif detector:
            for found in detector.flush(now - 0.1).found:
                shown_recently[found.ref.label] = now
        previous = (day, now)
        text = line.split(" Heard: ", 1)[1].rsplit(" -> ", 1)[0].strip()
        then = line.rsplit(" -> ", 1)[1].strip()
        up = [f.ref.label for f in detector.process(text, now, speed).found if f.triggered]
        for label in up:
            shown_recently[label] = now
        now_text = ", ".join(up) or "-"
        if now_text == then:
            same += 1
        elif then == "-":
            new += 1
            print(f"NEW      {day} {clock}  {text[:60]:60} -> {now_text}")
        elif all(now - shown_recently.get(label, -1e9) <= 15 for label in then.split(", ")):
            same += 1  # already put up a few seconds earlier
        else:
            changed += 1
            print(f"CHANGED  {day} {clock}  {text[:60]:60} was {then} -> now {now_text}")
    print(f"\nsame {same}, new catches {new}, changed {changed}  (pass speed {speed}s)")


if __name__ == "__main__":
    main()
