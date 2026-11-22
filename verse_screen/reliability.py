"""Things a live service depends on: logs, one running copy, and a laptop that stays awake."""
import logging
import socket
import sys
from logging.handlers import RotatingFileHandler

from .database import ROOT

LOG_DIR = ROOT / "logs"
LOCK_PORT = 47613


def setup_logging():
    """Errors, events and each sentence heard (so a wrong verse can be traced), kept on this laptop only.

    A log folder that cannot be written (full disk, read-only stick) must not stop the service: carry on without it.
    """
    handlers = [logging.StreamHandler()] if sys.stderr else []  # started without the black window: no console
    try:
        LOG_DIR.mkdir(exist_ok=True)
        handlers.insert(0, RotatingFileHandler(LOG_DIR / "app.log", maxBytes=1_000_000, backupCount=3,
                                               encoding="utf-8"))
    except OSError as error:
        print(f"Cannot write the log file ({error}); carrying on without it.", file=sys.stderr)
    logging.basicConfig(level=logging.INFO, handlers=handlers, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)


def acquire_single_instance():
    """Return a held socket, or None if the app is already running. The OS frees it even after a crash."""
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        holder.bind(("127.0.0.1", LOCK_PORT))
        return holder
    except OSError:
        holder.close()
        return None


def keep_laptop_awake():
    """Stop Windows sleeping or blanking the display during a service."""
    if sys.platform == "win32":
        import ctypes

        es_continuous, es_system, es_display = 0x80000000, 0x00000001, 0x00000002
        ctypes.windll.kernel32.SetThreadExecutionState(es_continuous | es_system | es_display)


def power_status():
    """(plugged_in, battery_percent). Either can be None when the laptop does not say."""
    if sys.platform != "win32":
        return None, None
    try:
        import ctypes

        class Status(ctypes.Structure):
            _fields_ = [("ac", ctypes.c_ubyte), ("flag", ctypes.c_ubyte), ("percent", ctypes.c_ubyte),
                        ("saver", ctypes.c_ubyte), ("life", ctypes.c_ulong), ("full", ctypes.c_ulong)]

        status = Status()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            return None, None
        plugged = {0: False, 1: True}.get(status.ac)
        return plugged, (status.percent if status.percent <= 100 else None)
    except Exception:
        return None, None


def free_megabytes(folder=ROOT):
    try:
        import shutil

        return shutil.disk_usage(folder).free // 1_000_000
    except OSError:
        return None
