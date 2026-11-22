"""Video for the projector: the church's own files, and links (YouTube, Facebook, Vimeo...) played as a stream.

A link is never saved to disk: yt-dlp finds the stream behind it, and mpv (the player inside the projector window)
plays it, keeping a few minutes in memory ahead of the picture so a weak connection does not stop the video.
"""
import ctypes
import ctypes.util
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MPV_DIR = ROOT / "mpv"  # libmpv-2.dll (Windows), fetched by setup
STREAM_FORMAT = "bv*[height<=1080][vcodec!*=av01]+ba[ext=m4a]/bv*[height<=1080][vcodec!*=av01]+ba/b[height<=1080]/b"
STREAM_MAX_AGE = 3 * 3600  # YouTube stream addresses stop working after about six hours: look them up again before
SKIP_SECONDS = 10


class VideoError(Exception):
    """Said to the operator as it is, so it must be plain words."""


def plain_reason(error):
    text = str(error).lower()
    for words, said in (
        (("private video",), "This video is private. Ask headquarters to make it Public or Unlisted."),
        (("sign in to confirm your age", "age-restricted", "inappropriate"),
         "YouTube only plays this video to signed-in adults, so it cannot be played here."),
        (("members-only", "join this channel"), "This video is for channel members only."),
        (("not available in your country", "geo", "blocked it in your country"),
         "This video is blocked in this country."),
        (("live event will begin", "premieres in", "this live event"), "This live broadcast has not started yet."),
        (("video unavailable", "has been removed", "does not exist", "404"),
         "This video is not there any more (removed, or the link is wrong)."),
        (("unsupported url", "no video formats", "is not a valid url"),
         "This link does not lead to a video the app can play. Copy the link from the video page itself."),
        (("getaddrinfo", "name resolution", "timed out", "network is unreachable", "connection", "urlopen error"),
         "No internet connection. Check the Wi-Fi or modem, then try again."),
    ):
        if any(word in text for word in words):
            return said
    return "The video could not be opened: " + str(error).replace("ERROR: ", "").split("\n")[0][:200]


@dataclass
class Stream:
    url: str  # the link the operator pasted
    title: str
    duration: float = 0.0  # 0 = live, or not known
    thumbnails: list = field(default_factory=list)  # best first; the largest is often missing
    video: str = ""
    audio: str = ""
    headers: dict = field(default_factory=dict)
    is_live: bool = False
    found_at: float = field(default_factory=time.monotonic)

    def stale(self):
        return time.monotonic() - self.found_at > STREAM_MAX_AGE


def _deno():
    folder = os.path.dirname(sys.executable)
    for name in ("deno.exe", "deno"):
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            return path
    return None


def find_stream(url):
    """Slow (5-20 s, it asks the website): never call it on the screen's thread."""
    try:
        import yt_dlp
    except ImportError:
        raise VideoError("Links cannot be played: the yt-dlp part is not installed. Run setup again.") from None
    options = {"quiet": True, "no_warnings": True, "noplaylist": True, "format": STREAM_FORMAT, "socket_timeout": 20}
    deno = _deno()
    if deno:  # YouTube needs a JavaScript runtime to hand out its streams; setup installs deno next to Python
        options["js_runtimes"] = {"deno": {"path": deno}}
    try:
        with yt_dlp.YoutubeDL(options) as finder:
            info = finder.extract_info(url.strip(), download=False)
    except Exception as error:
        logging.warning("Could not open link %s: %s", url, error)
        raise VideoError(plain_reason(error)) from None
    if info.get("_type") == "playlist":
        entries = [e for e in info.get("entries") or [] if e]
        if not entries:
            raise VideoError("This link is a playlist with no videos in it.")
        info = entries[0]
    parts = info.get("requested_formats") or [info]
    video = next((p for p in parts if p.get("vcodec") != "none"), parts[0])
    audio = next((p for p in parts if p.get("vcodec") == "none" and p.get("acodec") != "none"), None)
    if not video.get("url"):
        raise VideoError("This link does not lead to a video the app can play.")
    return Stream(url=url.strip(), title=info.get("title") or "Video", duration=float(info.get("duration") or 0),
                  thumbnails=_thumbnails(info), video=video["url"], audio=audio["url"] if audio else "",
                  headers=video.get("http_headers") or info.get("http_headers") or {},
                  is_live=bool(info.get("is_live")))


def _thumbnails(info):
    """Picture addresses for the operator's preview: medium sizes first (quick on a slow connection), JPG first."""
    found = [t for t in info.get("thumbnails") or [] if t.get("url")]
    found.sort(key=lambda t: (abs((t.get("width") or 4000) - 640), t["url"].endswith(".webp")))
    urls = [t["url"] for t in found[:4]]
    if info.get("thumbnail") and info["thumbnail"] not in urls:
        urls.append(info["thumbnail"])
    return urls


def _load_mpv():
    """libmpv ships in the app's mpv folder (Windows: setup puts libmpv-2.dll there)."""
    if MPV_DIR.is_dir():
        if os.name == "nt":
            os.environ["PATH"] = str(MPV_DIR) + os.pathsep + os.environ.get("PATH", "")
            os.add_dll_directory(str(MPV_DIR))
        else:  # a developer's copy: load its companions first so libmpv finds them
            libraries = sorted(MPV_DIR.glob("*.so*"))
            for _round in range(4):
                left = []
                for library in libraries:
                    try:
                        ctypes.CDLL(str(library), mode=ctypes.RTLD_GLOBAL)
                    except OSError:
                        left.append(library)
                libraries = left
            local = MPV_DIR / "libmpv.so.2"
            if local.exists():
                original = ctypes.util.find_library
                ctypes.util.find_library = lambda name: str(local) if name == "mpv" else original(name)
                try:
                    import mpv
                finally:
                    ctypes.util.find_library = original
                return mpv
    import mpv
    return mpv


def fmt_time(seconds):
    seconds = int(max(0, seconds or 0))
    hours, rest = divmod(seconds, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"


class Player:
    """One mpv inside the projector window. Every call is safe to make from the screen's thread."""

    def __init__(self, window_id, video_output=None, audio_output=None, audio_device=None):
        """window_id None: a sound-only player (music under a poster)."""
        mpv = _load_mpv()
        options = dict(osc=False, input_default_bindings=False, input_vo_keyboard=False,
                       keep_open="yes", idle="yes", ytdl=False, hwdec="auto-safe", cache="yes",
                       demuxer_max_bytes="400MiB", demuxer_readahead_secs=120, cache_pause_initial=True,
                       cache_pause_wait=4, background="color", keepaspect=True, volume=100,
                       network_timeout=30, msg_level="all=warn")
        if window_id is None:
            options.update(vid="no", force_window="no")
        else:
            options["wid"] = str(window_id)
        if audio_device:
            options["audio_device"] = audio_device
        if video_output:
            options["vo"] = video_output
        if audio_output:
            options["ao"] = audio_output
        self.mpv = mpv.MPV(log_handler=self._log, **options)
        self.loaded = None  # what is in the player: a file path or a Stream
        self.error = None
        self.last_position = 0.0

        @self.mpv.event_callback("end-file")
        def _ended(event):  # mpv's own thread: only note it down
            try:
                reason = getattr(getattr(event, "data", None), "reason", None)
                if reason == 4 or str(reason).lower() in ("error", "b'error'"):  # 4 = MPV_END_FILE_REASON_ERROR
                    self.error = "The video stopped: the connection dropped or the file is damaged. Press PLAY to try again."
            except Exception:
                logging.exception("Could not read the player's end-of-video note")

    @staticmethod
    def _log(level, prefix, text):
        logging.log(logging.ERROR if level in ("fatal", "error") else logging.INFO, "mpv %s: %s", prefix, text.strip())

    def load(self, item, start=0.0, play=False, loop=False):
        """item: a file path, or a Stream. Starts paused unless play=True, so it can fill its buffer first."""
        self.error = None
        stream = isinstance(item, Stream)
        # Set as properties, not loadfile options: stream addresses are full of commas.
        self.mpv["audio-files"] = [item.audio] if stream and item.audio else []
        self.mpv["http-header-fields"] = [f"{k}: {v}" for k, v in item.headers.items()] if stream else []
        self.mpv["start"] = f"{start:.1f}" if start else "none"
        self.mpv["loop-file"] = "inf" if loop else "no"
        self.mpv.pause = not play
        self.mpv.loadfile(item.video if stream else str(item))
        self.loaded = item

    # ---- controls ----
    def play(self):
        if self.error and self.loaded is not None:  # the stream dropped: carry on from where it stopped
            self.load(self.loaded, start=self.last_position, play=True)
            return
        self.mpv.pause = False

    def pause(self):
        self.mpv.pause = True

    def toggle(self):
        if self.paused:
            self.play()
        else:
            self.pause()

    def _seek(self, seconds):
        """A press before the video has opened (a slow link) is ignored, not an error."""
        if self.loaded is None or not self.duration:
            return False
        try:
            self.mpv.seek(max(0.0, min(self.duration - 1, seconds)), "absolute")
            return True
        except Exception as error:
            logging.info("Seek ignored: %s", error)
            return False

    def skip(self, seconds):
        return self._seek(self.position + seconds)

    def seek_to(self, seconds):
        return self._seek(seconds)

    def restart(self):
        self.seek_to(0)

    def stop(self):
        try:
            self.mpv.command("stop")
        except Exception:
            logging.exception("Player stop failed")
        self.loaded, self.error, self.last_position = None, None, 0.0

    def set_volume(self, level):
        self.mpv.volume = max(0, min(100, int(level)))

    def set_mute(self, muted):
        self.mpv.mute = bool(muted)

    def set_loop(self, loop):
        self.mpv["loop-file"] = "inf" if loop else "no"

    def set_audio_device(self, name):
        try:
            self.mpv["audio-device"] = name or "auto"
        except Exception:
            logging.warning("Sound output %s not available", name)

    def audio_devices(self):
        """[(name, description)] of the laptop's sound outputs: the mixer cable, HDMI, speakers..."""
        try:
            return [(d["name"], d.get("description") or d["name"]) for d in self.mpv.audio_device_list or []]
        except Exception:
            return []

    # ---- what the operator sees ----
    def _get(self, name, default=None):
        try:
            value = getattr(self.mpv, name)
        except Exception:
            return default
        return default if value is None else value

    @property
    def position(self):
        position = self._get("time_pos", 0.0)
        if position:
            self.last_position = position
        return position

    @property
    def duration(self):
        return self._get("duration", 0.0)

    @property
    def paused(self):
        return bool(self._get("pause", True))

    @property
    def ended(self):
        return bool(self._get("eof_reached", False))

    @property
    def buffering(self):
        return bool(self._get("paused_for_cache", False))

    @property
    def buffered_ahead(self):
        state = self._get("demuxer_cache_state", {}) or {}
        return float(state.get("cache-duration") or 0.0) if isinstance(state, dict) else 0.0

    def close(self):
        try:
            self.mpv.terminate()
        except Exception:
            pass


class MirroredPlayer:
    """Projector and TV both full screen: mpv draws into one window only, so the TV gets a second player.

    It opens the same video, always silent (the sound comes from the first), and follows every press. Each check
    (_watch_video, 2-3 times a second) pulls it back if it drifts. A link is fetched twice: on a weak connection
    a Windows "Duplicate" display or an HDMI splitter is lighter.
    """

    DRIFT_SECONDS = 0.4

    def __init__(self, first, second):
        object.__setattr__(self, "first", first)
        object.__setattr__(self, "second", second)
        second.set_mute(True)

    def __getattr__(self, name):  # anything not mirrored is read from the first player
        return getattr(self.first, name)

    def __setattr__(self, name, value):  # media_page resets .error / .loaded after a dropped stream
        setattr(self.first, name, value)
        if name in ("error", "loaded"):
            setattr(self.second, name, value)

    def _both(self, action, *args, **kwargs):
        result = getattr(self.first, action)(*args, **kwargs)
        try:
            getattr(self.second, action)(*args, **kwargs)
        except Exception:
            logging.exception("Second screen video could not follow (%s)", action)
        return result

    def load(self, item, start=0.0, play=False, loop=False):
        self._both("load", item, start=start, play=play, loop=loop)

    def play(self):
        self._both("play")

    def pause(self):
        self._both("pause")

    def toggle(self):
        if self.first.paused:
            self.play()
        else:
            self.pause()

    def skip(self, seconds):
        target = self.first.position + seconds
        moved = self.first.seek_to(target)
        self.second.seek_to(target)
        return moved

    def seek_to(self, seconds):
        return self._both("seek_to", seconds)

    def restart(self):
        self._both("restart")

    def stop(self):
        self._both("stop")

    def set_loop(self, loop):
        self._both("set_loop", loop)

    def set_mute(self, muted):
        self.first.set_mute(muted)  # the TV copy stays silent: two sounds a moment apart would echo

    def sync(self):
        first, second = self.first, self.second
        if first.loaded is None or second.loaded is None:
            return
        try:
            if first.paused != second.paused:
                (second.pause if first.paused else second.play)()
            here, there = first.position, second.position
            if here and abs(here - there) > self.DRIFT_SECONDS and not second.buffering:
                second.seek_to(here)
        except Exception:
            logging.exception("Second screen video sync failed")

    def close(self):
        self.first.close()
        self.second.close()
