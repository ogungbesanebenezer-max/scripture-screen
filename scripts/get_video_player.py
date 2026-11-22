"""Put the video player (libmpv-2.dll) in the app's mpv folder. Windows only; stdlib only; run by install.py.

Pinned to one build and checked against its SHA-256, so every laptop gets exactly the file tested here.
Windows' own tar.exe (Windows 10 and later) opens the .7z archive, so nothing else needs installing.
"""
import hashlib
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "mpv"
DLL = "libmpv-2.dll"
BUILD = ("https://github.com/shinchiro/mpv-winbuild-cmake/releases/download/20261111/"
         "mpv-dev-x86_64-20261111-git-bcde6b93cc.7z")
SHA256 = "273c079b11ea5575822f972756f4c9a71149a5c10130353abeb8586738938452"
TRIES = 3


def fetch(url, destination):
    for attempt in range(1, TRIES + 1):
        try:
            with urllib.request.urlopen(url, timeout=60) as answer, open(destination, "wb") as out:
                shutil.copyfileobj(answer, out, 1 << 20)
            return
        except OSError as error:
            if attempt == TRIES:
                raise
            print(f"  download failed ({error}); trying again...")
            time.sleep(3 * attempt)


def main():
    if sys.platform != "win32":
        print("Video player: install libmpv yourself (Ubuntu: sudo apt install libmpv2; Mac: brew install mpv).")
        return 0
    if (TARGET / DLL).exists():
        print("Video player already installed.")
        return 0
    print("Downloading the video player (about 30 MB)...")
    with tempfile.TemporaryDirectory() as work:
        archive = Path(work) / "mpv.7z"
        try:
            fetch(BUILD, archive)
        except OSError as error:
            print(f"  Could not download the video player: {error}. Videos will not play until setup is run again "
                  "with internet. Pictures and verses work as normal.")
            return 1
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if digest != SHA256:
            print("  The downloaded video player did not match the tested one, so it was not installed.")
            return 1
        result = subprocess.run(["tar", "-xf", str(archive), "-C", work, DLL], capture_output=True, text=True)
        if result.returncode or not (Path(work) / DLL).exists():
            print(f"  Could not unpack the video player: {result.stderr.strip() or 'tar failed'}")
            return 1
        TARGET.mkdir(exist_ok=True)
        shutil.move(str(Path(work) / DLL), TARGET / DLL)
    print("Video player installed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
