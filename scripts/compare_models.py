"""Score speech models on the recordings made by record_voice.py. Usage: python scripts/compare_models.py [model:beam ...]"""
import json
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from verse_screen.bible import Library  # noqa: E402
from verse_screen.references import ReferenceFinder  # noqa: E402
from verse_screen.speech import Transcriber  # noqa: E402

FOLDER = ROOT / "rehearsal"
DEFAULT = ["base.en:1", "small.en:1", "small.en:5"]


def load(path):
    with wave.open(str(path), "rb") as source:
        return np.frombuffer(source.readframes(source.getnframes()), dtype=np.int16).astype(np.float32) / 32768


def main():
    lines = json.loads((FOLDER / "lines.json").read_text(encoding="utf-8"))
    finder = ReferenceFinder(Library())
    report = []
    for spec in sys.argv[1:] or DEFAULT:
        name, beam = spec.split(":")
        transcriber = Transcriber(name)
        right, seconds, misses = 0, [], []
        for item in lines:
            audio = load(FOLDER / item["file"])
            began = time.perf_counter()
            text = transcriber.transcribe(audio, beam_size=int(beam))
            seconds.append(time.perf_counter() - began)
            found = [match.ref.label for match in finder.find(text)]
            if found[:1] == [item["expected"]]:
                right += 1
            else:
                misses.append(f"    want {item['expected']:22} heard: {text!r}  -> {found}")
        report += [f"{spec}: {right}/{len(lines)} right, average {sum(seconds) / len(seconds):.1f}s, slowest {max(seconds):.1f}s",
                   *misses, ""]
        print(report[-len(misses) - 2], flush=True)
    (FOLDER / "results.txt").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))


if __name__ == "__main__":
    main()
