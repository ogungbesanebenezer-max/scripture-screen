"""Start the app. Also works with no console window (pythonw), where a start-up fault would otherwise be invisible."""
import os
import sys
import traceback
from pathlib import Path

for _name in ("stdout", "stderr"):  # no console: give libraries that print somewhere harmless to write
    if getattr(sys, _name) is None:
        setattr(sys, _name, open(os.devnull, "w", encoding="utf-8"))

# A crash inside a native library (speech engine, video player) ends the app without a Python error, so the log
# shows nothing. faulthandler writes where it happened to logs/crash.log (2026-11-21: the app closed once unseen).
try:
    import faulthandler

    _crash_dir = Path(__file__).resolve().parent.parent / "logs"
    _crash_dir.mkdir(exist_ok=True)
    _crash_log = open(_crash_dir / "crash.log", "a", encoding="utf-8")
    faulthandler.enable(_crash_log, all_threads=True)
except Exception:
    pass

try:
    from .app import main
except Exception:  # a missing or broken library: say so instead of silently not opening
    details = traceback.format_exc()
    try:
        folder = Path(__file__).resolve().parent.parent / "logs"
        folder.mkdir(exist_ok=True)
        (folder / "startup-error.txt").write_text(details, encoding="utf-8")
    except OSError:
        pass
    try:
        import tkinter
        from tkinter import messagebox

        _window = tkinter.Tk()
        _window.withdraw()
        messagebox.showerror("Scripture Screen", "Scripture Screen could not start. Run setup.bat again.\n\n"
                             + details[-600:])
    except Exception:
        pass
    raise SystemExit(1)

main()
