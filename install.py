"""One-command setup for Windows, Mac and Linux. Needs internet once; the app is offline afterwards."""
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
PYTHON = VENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def main():
    if sys.version_info[:2] != (3, 12):
        sys.exit(f"Python 3.12 is needed. This is {sys.version.split()[0]}.")
    try:
        import tkinter  # noqa: F401
    except ImportError:
        sys.exit("Python's window toolkit is missing. On Ubuntu run: sudo apt install python3-tk libportaudio2\n"
                 "On Mac run: brew install python-tk\nOn Windows reinstall Python with 'tcl/tk' ticked.")
    if not PYTHON.exists():
        print("Creating private Python environment...")
        venv.create(VENV, with_pip=True)
    print("Installing libraries (this needs internet)...")
    subprocess.check_call([str(PYTHON), "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                           "-r", str(ROOT / "requirements.txt"), "-c", str(ROOT / "constraints.txt")])
    # NKJV and Bibeli Mimo, onto this laptop only. No internet or a source taken down: setup simply goes on.
    private = ROOT / "scripts" / "get_private_bibles.py"  # only in the owner's private copy, never published
    if private.exists():
        subprocess.call([str(PYTHON), str(private)], cwd=ROOT)
    # The video player for MP4s and video links. No internet: verses and pictures still work; run setup again later.
    subprocess.call([str(PYTHON), str(ROOT / "scripts" / "get_video_player.py")], cwd=ROOT)
    subprocess.check_call([str(PYTHON), "-m", "verse_screen.first_run"], cwd=ROOT)
    print("\nSetup finished. Start the app with start.bat (Windows) or ./start.sh (Mac/Linux).")


if __name__ == "__main__":
    main()
