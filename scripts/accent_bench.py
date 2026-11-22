"""Nigerian-accent test bench. Speaks sermon lines in Nigerian English voices, roughens them like a church PA,
then checks the app finds the right verse. Needs internet once (to make the audio) and edge-tts:
    pip install edge-tts
    python scripts/accent_bench.py make          # build bench/ audio
    python scripts/accent_bench.py run base.en small.en
"""
import asyncio
import json
import subprocess
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FOLDER = ROOT / "bench"

LINES = [
    ("Media, give me Genesis chapter one verse one.", "Genesis 1:1"),
    ("Media, project Jude chapter one verse seven.", "Jude 1:7"),
    ("Let us open our Bibles to John chapter three verse sixteen.", "John 3:16"),
    ("Turn with me to Romans chapter eight verse twenty eight.", "Romans 8:28"),
    ("The Bible says in Proverbs chapter eighteen verse twenty one.", "Proverbs 18:21"),
    ("Media, give me Habakkuk chapter two verse three.", "Habakkuk 2:3"),
    ("Philippians chapter four verse thirteen, I can do all things.", "Philippians 4:13"),
    ("Go to Isaiah chapter fifty three verse five.", "Isaiah 53:5"),
    ("Media, Jeremiah twenty nine eleven.", "Jeremiah 29:11"),
    ("First Corinthians chapter thirteen verses four to seven.", "1 Corinthians 13:4-7"),
    ("Second Timothy chapter one verse seven, media.", "2 Timothy 1:7"),
    ("Hebrews chapter eleven verse one, now faith is the substance.", "Hebrews 11:1"),
    ("Media, give me Ephesians chapter six verse eleven.", "Ephesians 6:11"),
    ("Look at Deuteronomy chapter twenty eight verse thirteen.", "Deuteronomy 28:13"),
    ("Psalm ninety one verse one, he that dwelleth.", "Psalm 91:1"),
    ("Media, Matthew chapter six verse thirty three.", "Matthew 6:33"),
    ("Let us read Ezekiel chapter thirty seven verse four.", "Ezekiel 37:4"),
    ("Galatians chapter five verse twenty two, the fruit of the spirit.", "Galatians 5:22"),
    ("Media, give me Exodus chapter fourteen verse fourteen.", "Exodus 14:14"),
    ("Zechariah chapter four verse six, not by might.", "Zechariah 4:6"),
    ("Malachi chapter three verse ten, bring all the tithes.", "Malachi 3:10"),
    ("Colossians chapter three verse twenty three.", "Colossians 3:23"),
    ("Media, Joshua chapter one verse eight.", "Joshua 1:8"),
    ("Nehemiah chapter eight verse ten, the joy of the Lord.", "Nehemiah 8:10"),
    ("Revelation chapter twelve verse eleven.", "Revelation 12:11"),
    ("Media, give me Second Corinthians five seventeen.", "2 Corinthians 5:17"),
    ("Lamentations chapter three verse twenty two.", "Lamentations 3:22"),
    ("First Peter chapter five verse seven, cast all your cares.", "1 Peter 5:7"),
    ("Media, Ecclesiastes chapter three verse one.", "Ecclesiastes 3:1"),
    ("James chapter one verse five, if any of you lack wisdom.", "James 1:5"),
    ("Leviticus chapter seventeen verse eleven.", "Leviticus 17:11"),
    ("Media, Acts chapter one verse eight.", "Acts 1:8"),
]
VOICES = ["en-NG-AbeoNeural", "en-NG-EzinneNeural"]
RATES = ["+0%", "+20%"]
# Church sound: room echo plus crowd/AC hum underneath. Applied on top of each clean clip.
ROOM = ("[0:a]aecho=0.8:0.7:45|90:0.35|0.2,highpass=f=120,lowpass=f=6500[s];"
        "anoisesrc=color=pink:amplitude=0.03:sample_rate=16000[n];"
        "[s][n]amix=inputs=2:duration=first:weights=1 1,volume=1.6")


async def make():
    import edge_tts

    FOLDER.mkdir(exist_ok=True)
    items = []
    for n, (text, expected) in enumerate(LINES):
        for voice in VOICES:
            for rate in RATES:
                stem = f"{n:02d}_{voice.split('-')[2][:4]}_{rate.strip('%+')}"
                mp3, clean, church = (FOLDER / f"{stem}.{x}" for x in ("mp3", "clean.wav", "church.wav"))
                if not church.exists():
                    for _attempt in range(6):
                        try:
                            await edge_tts.Communicate(text, voice, rate=rate).save(str(mp3))
                            break
                        except Exception:
                            await asyncio.sleep(3)
                    if not mp3.exists() or mp3.stat().st_size < 1000:
                        print(f"  skipped {stem}: voice service did not answer", flush=True)
                        continue
                    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", mp3, "-ar", "16000", "-ac", "1",
                                    "-af", "apad=pad_dur=0.6", clean], check=True)
                    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", clean, "-filter_complex", ROOM,
                                    "-ar", "16000", "-ac", "1", church], check=True)
                    mp3.unlink(missing_ok=True)
                for kind, path in (("clean", clean), ("church", church)):
                    items.append({"file": path.name, "expected": expected, "text": text, "voice": voice,
                                  "rate": rate, "sound": kind})
        print(f"{n + 1}/{len(LINES)}", flush=True)
    (FOLDER / "lines.json").write_text(json.dumps(items, indent=1), encoding="utf-8")


def load(path):
    import numpy as np

    with wave.open(str(path), "rb") as source:
        return np.frombuffer(source.readframes(source.getnframes()), dtype=np.int16).astype(np.float32) / 32768


def run(models):
    from verse_screen.bible import Library
    from verse_screen.references import ReferenceFinder
    from verse_screen.speech import Transcriber

    items = json.loads((FOLDER / "lines.json").read_text(encoding="utf-8"))
    finder = ReferenceFinder(Library())
    for spec in models:
        name, _, beam = spec.partition(":")
        transcriber = Transcriber(name)
        right, seconds, misses, by_sound = 0, [], [], {}
        for item in items:
            began = time.perf_counter()
            text = transcriber.transcribe(load(FOLDER / item["file"]), beam_size=int(beam or 1))
            seconds.append(time.perf_counter() - began)
            found = [m.ref.label for m in finder.find(text)]
            ok = found[:1] == [item["expected"]]
            right += ok
            tally = by_sound.setdefault(item["sound"], [0, 0])
            tally[0] += ok
            tally[1] += 1
            if not ok:
                misses.append(f"  want {item['expected']:20} {item['sound']:6} {item['voice'][6:10]} {item['rate']:4}"
                              f" heard {text!r} -> {found[:2]}")
        print(f"\n{spec}: {right}/{len(items)} right ({100 * right // len(items)}%), "
              + ", ".join(f"{k} {a}/{b}" for k, (a, b) in by_sound.items())
              + f", average {sum(seconds) / len(seconds):.2f}s, slowest {max(seconds):.2f}s")
        print("\n".join(misses), flush=True)


if __name__ == "__main__":
    if sys.argv[1:2] == ["make"]:
        asyncio.run(make())
    else:
        run(sys.argv[2:] or ["base.en", "small.en"])
