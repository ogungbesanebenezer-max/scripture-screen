"""Record someone reading test references, so speech models can be compared on their real voice and accent.

Double-click voice_test.bat. Recordings stay in the rehearsal folder on this laptop and are never uploaded with the repo.
"""
import json
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import sounddevice as sd  # noqa: E402

from verse_screen.speech import SAMPLE_RATE, resample  # noqa: E402

FOLDER = ROOT / "rehearsal"
SECONDS = 8
MINIMUM_LEVEL = 0.003
LINES = [
    ("Media, give me Proverbs chapter eighteen verse eleven.", "Proverbs 18:11"),
    ("Media, give me Genesis chapter one verse one.", "Genesis 1:1"),
    ("Media, give me John chapter three verse sixteen.", "John 3:16"),
    ("Media, project Psalm twenty three verse one.", "Psalm 23:1"),
    ("Media, give me Habakkuk chapter two verse four.", "Habakkuk 2:4"),
    ("Media, give me Ecclesiastes chapter three verse one.", "Ecclesiastes 3:1"),
    ("Media, give me Philippians chapter four verse thirteen.", "Philippians 4:13"),
    ("Media, give me First Corinthians chapter thirteen verse four.", "1 Corinthians 13:4"),
    ("Media, give me Isaiah chapter fifty three verse five.", "Isaiah 53:5"),
    ("Media, give me Jeremiah chapter twenty nine verse eleven.", "Jeremiah 29:11"),
    ("Media, give me Romans chapter eight verse twenty eight.", "Romans 8:28"),
    ("Media, give me Hebrews chapter eleven verse one.", "Hebrews 11:1"),
    ("Media, give me Deuteronomy chapter twenty eight verse one.", "Deuteronomy 28:1"),
    ("Media, give me Zechariah chapter four verse six.", "Zechariah 4:6"),
    ("Media, give me Malachi chapter three verse ten.", "Malachi 3:10"),
    ("Media, give me Ephesians chapter two verse eight.", "Ephesians 2:8"),
    ("Media, give me Galatians chapter five verse twenty two.", "Galatians 5:22"),
    ("Media, give me Second Timothy chapter one verse seven.", "2 Timothy 1:7"),
    ("Media, give me Matthew chapter six verse thirty three.", "Matthew 6:33"),
    ("Media, give me Revelation chapter three verse twenty.", "Revelation 3:20"),
]


def save(path, samples):
    audio = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(audio.tobytes())


def record(rate):
    clip = sd.rec(int(SECONDS * rate), samplerate=rate, channels=1, dtype="float32")
    sd.wait()
    return resample(clip[:, 0], rate)


def main():
    info = sd.query_devices(kind="input")
    rate = int(info["default_samplerate"])
    print(f"Microphone: {info['name']}")
    if "--check" in sys.argv:
        return
    FOLDER.mkdir(exist_ok=True)
    print(f"\nRead each line in your normal preaching voice. You get {SECONDS} seconds per line.\n")
    answers = []
    for number, (line, expected) in enumerate(LINES, 1):
        input(f"[{number}/{len(LINES)}] Press Enter, then say:\n    {line}\n")
        while True:
            print("    Recording...")
            samples = record(rate)
            if float(np.sqrt(np.mean(samples ** 2))) >= MINIMUM_LEVEL:
                break
            input("    I could not hear anything. Check the microphone, press Enter and say it again.\n")
        name = f"{number:02d}.wav"
        save(FOLDER / name, samples)
        answers.append({"file": name, "line": line, "expected": expected})
        print("    Saved.\n")
    (FOLDER / "lines.json").write_text(json.dumps(answers, indent=1), encoding="utf-8")
    print("Finished. Thank you. Tell Claude the voice test is done.")


if __name__ == "__main__":
    main()
