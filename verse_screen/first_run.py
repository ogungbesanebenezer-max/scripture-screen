"""Build the database, download the AI models, and pick the speech model this laptop can handle."""
import json
import time

import numpy as np

from .database import DB_PATH, build
from .speech import PARAKEET, SAMPLE_RATE, Transcriber

SETTINGS = DB_PATH.parent / "settings.json"
# Above this many seconds per pass, the better model would lag behind the speaker.
BASE_MODEL_LIMIT = 3.0


def benchmark(transcriber):
    """Time one pass. The token cap keeps random noise from running far longer than real speech."""
    audio = np.random.default_rng(1).normal(0, 0.05, SAMPLE_RATE * 7).astype(np.float32)
    began = time.perf_counter()
    segments, _ = transcriber.model.transcribe(audio, language="en", beam_size=1, max_new_tokens=30,
                                               without_timestamps=True, condition_on_previous_text=False)
    list(segments)
    return time.perf_counter() - began


def main():
    print("Building church_bible.db...")
    for note in build():
        print(" ", note)
    print("Getting the speech models ready (a download of about 220 MB the first time, if not included)...")
    Transcriber("tiny.en")
    print("Timing this laptop...")
    seconds = benchmark(Transcriber("base.en"))
    chosen = "base.en" if seconds <= BASE_MODEL_LIMIT else "tiny.en"
    print(f"  base.en took {seconds:.1f}s")
    print("Getting the Parakeet speech model ready (about 630 MB to download, if not included)...")
    try:
        parakeet = Transcriber(PARAKEET)
        if parakeet.name == PARAKEET:  # Transcriber hands back Whisper when Parakeet cannot load
            began = time.perf_counter()
            parakeet.transcribe(np.random.default_rng(1).normal(0, 0.05, SAMPLE_RATE * 7).astype(np.float32), vad=False)
            parakeet_seconds = time.perf_counter() - began
            print(f"  Parakeet took {parakeet_seconds:.1f}s")
            if parakeet_seconds <= BASE_MODEL_LIMIT:
                chosen = PARAKEET
        else:
            print("  Parakeet could not be set up; Whisper will be used.")
    except Exception as error:  # no internet or not enough disk: Whisper still works
        print(f"  Parakeet skipped: {error}")
    print(f"  using {chosen}")
    print("Getting the paraphrase model ready...")
    from .semantic import load_embedder

    load_embedder()
    settings = json.loads(SETTINGS.read_text(encoding="utf-8")) if SETTINGS.exists() else {}
    settings["model"] = chosen
    SETTINGS.write_text(json.dumps(settings, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
