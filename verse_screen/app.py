"""Operator dashboard and projector window."""
import argparse
import io
import json
import logging
import math
import os
import queue
import re
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox
from tkinter import ttk

from PIL import Image, ImageFilter, ImageTk

from .bible import ROOT, Library, Ref
from .book_suggest import book_part, suggest
from .detector import Detector, Found
from .displays import enable_windows_dpi_awareness, list_displays
from .references import ReferenceFinder
from .reliability import (acquire_single_instance, free_megabytes, keep_laptop_awake, power_status,
                          setup_logging)
from . import ready
from .media import MediaLibrary
from .video import find_stream  # noqa: F401  (the media page looks it up here; tests replace it)
from .database import build as build_bibles
from .database import is_stale as bibles_changed
from .phone_mic import PhoneMic, new_token, qr_png
from .speech import FilePlayer, Listener, Microphone, RingBuffer, Transcriber, list_microphones

SETTINGS_PATH = ROOT / "settings.json"
MAX_HISTORY = 200
MAX_UNDO = 20
HEALTH_CHECK_MS = 3000
ASLEEP_SECONDS = 30  # a health check this late means the laptop slept (lid closed) or was frozen
READY_COLORS = {"ok": "#2f9e62", "warn": "#e6b85c", "bad": "#d6453d"}
DISPLAY_CHECK_MS = 5000
AUTO_CLEAR_SECONDS = 0  # 0 = never: pastors can stay on one verse for minutes
STANDBY_TEXT = "Welcome"  # shown whenever no verse is up and there is no standby image
STANDBY_IMAGE = ROOT / "data" / "standby.jpg"
MARK_OPACITY = 0.55  # the corner logo stays faint, so it never competes with the scripture
# A verse stays at least this long, longer than the 7 s window, so an old "Amen" can't clear a new verse.
MIN_SECONDS_LIVE = 8
COMPLETE_SECONDS = 15  # how long a cut-off verse ("verse 1" of "verse 18") can still be completed on screen
CLEAR_PHRASES = ["amen", "praise the lord", "let us pray", "hallelujah"]
NO_SECOND = "None"
SECOND_INK = "#ffe9b0"  # the second language, so the two are easy to tell apart
from .style import BG, BLUE, FIELD, GOLD, GREEN, INK, MUTED, PANEL, RED  # noqa: E402
KIND_TAGS = {"exact": "Named", "fuzzy": "Named (corrected)", "chapter": "Chapter", "follow": "Follow-on",
             "next": "Next verse", "paraphrase": "Paraphrase?", "typed": "Typed", "command": "Pastor asked",
             "single": "Named", "phonetic": "Sounds like", "book_guess": "Book unclear?"}
# While a poster or other media is up (or the operator blanked the screen), speech never replaces it by itself.
# "ask": a verse the pastor asks for waits for the operator's Space. "switch": it goes up at once.
from .media_page import PROTECTED_MODES, VIDEO_CHECK_MS, MediaControl  # noqa: E402
from .sermon_page import SermonTestControl  # noqa: E402
from .screens_page import ScreensControl  # noqa: E402
from .church_page import ChurchControl  # noqa: E402
VERSE_DURING_MEDIA = ("ask", "switch")


def load_settings():
    """A missing, half-written or hand-edited settings file must never stop the app starting."""
    try:
        saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(saved, dict):
        return {}
    for key in ("model", "translation", "second_translation", "microphone", "display", "phone_mic_token",
                "standby_text", "standby_image", "verse_during_media", "outputs_token", "second_display",
                "church_name", "church_logo", "church_color", "church_logo_corner"):
        if key in saved and not isinstance(saved[key], str):
            del saved[key]
    for key in ("min_confidence", "auto_clear_seconds"):
        if key in saved and (isinstance(saved[key], bool) or not isinstance(saved[key], (int, float))
                             or not math.isfinite(saved[key])):
            del saved[key]
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", saved.get("church_color", "#000000")):
        del saved["church_color"]
    if saved.get("church_logo_corner", "off") not in ("off", "top-left", "top-right", "bottom-left", "bottom-right"):
        del saved["church_logo_corner"]
    if saved.get("verse_during_media", "ask") not in VERSE_DURING_MEDIA:
        del saved["verse_during_media"]
    phrases = saved.get("clear_phrases")
    if "clear_phrases" in saved and not (isinstance(phrases, list) and all(isinstance(p, str) and p.strip()
                                                                           for p in phrases)):
        del saved["clear_phrases"]
    return saved


def split_evenly(lines, count):
    """Spread lines over the pages, so two languages stay at about the same point in the verse."""
    size = len(lines)
    return ["\n".join(lines[size * i // count:size * (i + 1) // count]) for i in range(count)]


def faded(color, level):
    channels = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{int(c * level):02x}" for c in channels)


def single_screen_geometry(display, root):
    """Top right of the screen, in the room the operator window leaves; at least 360 px wide, at most half."""
    try:
        root.update_idletasks()
        right = root.winfo_x() + root.winfo_width() if root.winfo_width() > 1 else 1160
        # Tk's own screen size: at 125% Windows scaling the display list says 1920 wide but Tk places in 1536.
        screen_w = root.winfo_screenwidth()
    except tk.TclError:
        right, screen_w = 1160, display.width
    width = max(360, min(screen_w // 2, screen_w - right - 20))
    height = width * 9 // 16
    return f"{width}x{height}+{max(0, screen_w - width - 10)}+40"


class Projector:
    """Text fades against black, so the desktop behind the window can never show on the TV."""

    FADE_IN, FADE_OUT, FADE_SWAP, FRAME = 300, 500, 120, 25
    LINES_PER_PAGE = 3
    IMAGE_STEPS = 8
    WORDS_PER_SECOND = 2.3

    def __init__(self, root, standby=STANDBY_TEXT, image_path=STANDBY_IMAGE, name="Scripture Display"):
        self.standby, self.name = standby, name
        self.mirror = None  # a second full screen (projector + TV): shows the same thing, see screens_page
        self.outputs = None  # the livestream and stage pages (outputs.py)
        self.source = Image.open(image_path).convert("RGB") if image_path.exists() else None
        self.frames, self.frames_size, self.frames_source, self.image_item = [], None, None, None
        self.root, self.placed = root, None
        self.picture, self.blackout = None, False  # a media picture on screen / the operator blanked the screen
        self.scale, self.backdrop = 1.0, None  # hymn text size and background
        self.accent = GOLD  # the church's colour for verse and hymn titles (Our Church)
        self.mark, self.mark_corner = None, "off"  # the church logo, small and faint in a corner of the words
        self.mark_frames, self.mark_size, self.mark_item = [], None, None
        self.title, self.body, self.paged, self.second = "", "", False, None
        self.pages, self.page = [], 0
        self.items, self.level = [], 0.0
        self.fade_job = self.page_job = None
        self.last_change = 0.0
        self._open_window()

    def _open_window(self):
        self.window = tk.Toplevel(self.root)
        self.window.title(self.name)
        self.window.configure(bg="black")
        # The X button and Alt+F4 do nothing: closing this window used to end every verse for the rest of the service.
        self.window.protocol("WM_DELETE_WINDOW", lambda: None)
        self.canvas = tk.Canvas(self.window, bg="black", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _e: self._layout())
        self.video_frame = tk.Frame(self.window, bg="black")  # the video player draws into this
        self.video_shown = False

    def _alive(self):
        """If the verse window was destroyed all the same, open a new one in the same place."""
        try:
            if self.canvas.winfo_exists():
                return
        except tk.TclError:
            pass
        logging.error("Verse window was gone; opening it again")
        self.items, self.pages, self.image_item, self.level = [], [], None, 0.0
        self.frames, self.frames_size, self.frames_source = [], None, None
        self.mark_frames, self.mark_size, self.mark_item = [], None, None
        self.fade_job = self.page_job = None
        self._open_window()
        if self.placed:
            self.place(*self.placed)

    def place(self, display, is_only_screen):
        self.placed = (display, is_only_screen)
        self._alive()
        if is_only_screen:
            # No TV: a small always-on-top preview beside the operator window, so what the congregation would see
            # (a video above all) stays in sight. Behind other windows it played unseen (owner, 2026-11-17).
            self.window.overrideredirect(False)
            self.window.title("Projector preview (no TV connected)")
            where = single_screen_geometry(display, self.root)
            self.window.geometry(where)
            self.window.attributes("-topmost", True)
            self.window.after(400, lambda: self.window.geometry(where))  # Windows moves new windows to its own spot
        else:
            self.window.title(self.name)
            self.window.attributes("-topmost", False)
            self.window.overrideredirect(True)
            self.window.geometry(display.geometry)
        self.window.lift()

    def video_window_id(self):
        self._alive()
        self.window.update_idletasks()
        return self.video_frame.winfo_id()

    def show_video(self):
        self._alive()
        self.video_frame.place(x=0, y=0, relwidth=1, relheight=1)
        self.video_frame.lift()
        self.video_shown = True
        self._also("show_video")

    def hide_video(self):
        try:
            self.video_frame.place_forget()
        except tk.TclError:
            pass
        self.video_shown = False
        self._also("hide_video")

    def show(self, title, body, paged=False, second=None, scale=1.0, backdrop=None):
        """scale: text size for hymns (1 = as large as fits). backdrop: a picture shown dimmed behind the words."""
        self._alive()
        self.picture, self.blackout = None, False
        self.scale, self.backdrop = scale, backdrop
        self.title, self.body, self.paged, self.second = title, body, paged, second
        self._cancel_page()
        if self.level > 0:
            self._fade(0, self.FADE_SWAP, self._reveal)
        else:
            self._reveal()
        self._also("show", title, body, paged=paged, second=second, scale=scale, backdrop=backdrop)

    def clear(self):
        self._still(None, False, self.FADE_OUT)

    def show_picture(self, picture):
        """A poster or picture from the media library, filling the screen the same way the standby logo does."""
        self._still(picture, False, self.FADE_SWAP if self.level > 0 else 0)

    def black(self):
        self._still(None, True, self.FADE_OUT)

    def set_brand(self, accent=None, mark=None, corner="off"):
        """Our Church: title colour and the corner logo. Shown from the next verse; a verse on screen redraws."""
        self.accent = accent or GOLD
        self.mark, self.mark_corner = mark, corner if mark is not None else "off"
        self.mark_frames, self.mark_size = [], None
        if self.body and not self.fade_job:
            self._draw()
            self._fade(1, 1)
        self._also("set_brand", accent, mark, corner)

    def _prepare_mark(self, height):
        """The logo about a ninth of the screen high, pre-faded in steps like the pictures (Tk cannot fade images)."""
        if self.mark_size == height and self.mark_frames:
            return
        mark = self.mark.convert("RGBA")
        tall = max(24, height // 9)
        mark = mark.resize((max(1, round(mark.width * tall / mark.height)), tall), Image.LANCZOS)
        alpha = mark.getchannel("A")
        self.mark_frames = []
        for step in range(self.IMAGE_STEPS):
            frame = mark.copy()
            frame.putalpha(alpha.point(lambda v, f=step / (self.IMAGE_STEPS - 1): int(v * f * MARK_OPACITY)))
            self.mark_frames.append(ImageTk.PhotoImage(frame, master=self.canvas))
        self.mark_size = height

    def _draw_mark(self, width, height):
        if self.mark is None or self.mark_corner == "off":
            return
        self._prepare_mark(height)
        photo = self.mark_frames[0]
        margin = max(12, height // 40)
        right, bottom = "right" in self.mark_corner, "bottom" in self.mark_corner
        x = width - margin - photo.width() / 2 if right else margin + photo.width() / 2
        y = height - margin - photo.height() / 2 if bottom else margin + photo.height() / 2
        self.mark_item = self.canvas.create_image(x, y, image=self._mark_frame())

    def _mark_frame(self):
        return self.mark_frames[round(self.level * (self.IMAGE_STEPS - 1))]

    def set_standby(self, picture):
        self.source = picture
        if not (self.body or self.picture or self.blackout or self.fade_job):
            self._show_standby()
        self._also("set_standby", picture)

    def _also(self, action, *args, **kwargs):
        """The same on the second screen, and tell the livestream and stage pages. Neither may stop this screen."""
        if self.mirror:
            try:
                getattr(self.mirror, action)(*args, **kwargs)
            except Exception:
                logging.exception("Second screen could not follow (%s)", action)
        if self.outputs:
            try:
                mode = ("video" if self.video_shown else "black" if self.blackout else "text" if self.body
                        else "picture" if self.picture is not None else "standby")
                self.outputs.screen(mode, self.title, self.body, self.second or "", self.picture, self.standby)
            except Exception:
                logging.exception("Livestream/stage pages could not follow")

    def copy_to(self, other):
        """Bring a newly added second screen up to what this one shows now."""
        other.source = self.source
        if self.body:
            other.show(self.title, self.body, self.paged, self.second, self.scale, self.backdrop)
        elif self.blackout:
            other.black()
        elif self.picture is not None:
            other.show_picture(self.picture)
        else:
            other.clear()
        if self.video_shown:
            other.show_video()

    def _still(self, picture, blackout, fade_out):
        self._alive()
        self.title, self.body, self.second = "", "", None
        self.picture, self.blackout = picture, blackout
        self._cancel_page()
        if fade_out:
            self._fade(0, fade_out, self._show_standby)
        else:
            self._show_standby()
        self._also("black" if blackout else "show_picture" if picture is not None else "clear",
                   *([picture] if picture is not None and not blackout else []))

    def _show_standby(self):
        """Whatever is up when no verse is: the media picture, a black screen, or the standby logo."""
        canvas = self.canvas
        canvas.delete("all")
        self.pages, self.items, self.image_item, self.mark_item = [], [], None, None
        width, height = canvas.winfo_width(), canvas.winfo_height()
        source = self.picture if self.picture is not None else self.source
        if self.blackout:
            self.level = 0.0
        elif width >= 10 and source:
            self._prepare_frames(source, width, height)
            self.image_item = canvas.create_image(width / 2, height / 2, image=self._frame())
            self._fade(1, self.FADE_IN)
        elif width >= 10 and self.standby:
            item = canvas.create_text(width / 2, height / 2, text=self.standby, fill=faded(GOLD, self.level),
                                      justify="center", width=int(width * 0.86), font=("Georgia", -max(24, height // 9), "bold"))
            self.items.append((item, GOLD))
            self._fade(1, self.FADE_IN)

    def _prepare_frames(self, source, width, height):
        """Whole image in the middle over a blurred copy of itself; pre-faded because Tk cannot fade images."""
        if self.frames_source is source and self.frames_size == (width, height):  # "is": comparing images is slow
            return
        source_w, source_h = source.size
        fit = min(width / source_w, height / source_h)
        logo = source.resize((round(source_w * fit), round(source_h * fit)), Image.LANCZOS)
        edge = logo.crop((0, 0, logo.width, 1)).resize((1, 1)).getpixel((0, 0))  # the image's own outer colour
        backdrop = Image.new("RGB", (width, height), edge)
        margin = (width - logo.width) // 2
        if source is self.backdrop and source is not None:  # behind hymn words: fill the screen, dimmed
            cover = max(width / source_w, height / source_h)
            big = source.resize((max(1, round(source_w * cover)), max(1, round(source_h * cover))), Image.LANCZOS)
            left, top = (big.width - width) // 2, (big.height - height) // 2
            backdrop = big.crop((left, top, left + width, top + height)).point(lambda v: v * 0.38)
            logo, margin = None, 0
        elif source is self.picture and (margin > 0 or logo.height < height):
            # A poster: a dark, blurred copy of itself fills the sides (stretching its edges smears any border).
            cover = max(width / source_w, height / source_h) / 8  # blur a small copy: fast on a weak laptop
            small = source.resize((max(1, round(source_w * cover)), max(1, round(source_h * cover))))
            small = small.filter(ImageFilter.GaussianBlur(6)).point(lambda v: v * 0.45)
            left, top = (small.width - width // 8) // 2, (small.height - height // 8) // 2
            backdrop = small.crop((left, top, left + width // 8, top + height // 8)).resize((width, height), Image.BILINEAR)
        elif margin > 0:  # extend the image's own left and right edges so a square logo fills a wide TV
            strip = max(4, logo.width // 14)
            blur = ImageFilter.GaussianBlur(strip // 2)
            top = (height - logo.height) // 2
            backdrop.paste(logo.crop((0, 0, strip, logo.height)).resize((margin + 2, logo.height)).filter(blur), (0, top))
            right = logo.crop((logo.width - strip, 0, logo.width, logo.height)).resize((margin + 2, logo.height)).filter(blur)
            backdrop.paste(right, (width - margin - 2, top))
        if logo is not None:
            backdrop.paste(logo, (margin, (height - logo.height) // 2))
        black = Image.new("RGB", (width, height))
        self.frames = [ImageTk.PhotoImage(Image.blend(black, backdrop, i / (self.IMAGE_STEPS - 1)))
                       for i in range(self.IMAGE_STEPS)]
        self.frames_source, self.frames_size = source, (width, height)

    def _frame(self):
        return self.frames[round(self.level * (self.IMAGE_STEPS - 1))]

    def _reveal(self):
        self.page = 0
        self._layout()
        self._fade(1, self.FADE_IN)

    def _layout(self):
        if not self.body:
            if not self.fade_job:  # a resize during fade-out must not wipe the fading text
                self._show_standby()
            return
        self.pages = self._paginate() if self.paged else [tuple(self._texts())]
        self.page = min(self.page, len(self.pages) - 1)
        self._draw()
        self._schedule_page()

    def _texts(self):
        return [self.body, self.second] if self.second else [self.body]

    def _body_size(self):
        return max(18, self.canvas.winfo_height() // (13 if self.second else 11))

    def _wrap(self, text):
        font = tkfont.Font(family="Georgia", size=-self._body_size())
        limit = self.canvas.winfo_width() * 0.86
        lines, line = [], ""
        for word in text.split():
            attempt = f"{line} {word}".strip()
            if line and font.measure(attempt) > limit:
                lines.append(line)
                line = word
            else:
                line = attempt
        lines.append(line)
        return lines

    def _paginate(self):
        """Greedy line wrap, at most three lines per page. Each page is one text per language."""
        wrapped = [self._wrap(text) for text in self._texts()]
        count = max(math.ceil(len(lines) / self.LINES_PER_PAGE) for lines in wrapped)
        return list(zip(*(split_evenly(lines, count) for lines in wrapped)))

    def _draw(self):
        canvas = self.canvas
        canvas.delete("all")
        self.items, self.image_item = [], None
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if not self.pages or width < 10:
            return
        if self.backdrop is not None:  # first, so the words sit on top; fades with them
            self._prepare_frames(self.backdrop, width, height)
            self.image_item = canvas.create_image(width / 2, height / 2, image=self._frame())
        self.mark_item = None
        self._draw_mark(width, height)
        title = self.title + (f"   {self.page + 1}/{len(self.pages)}" if len(self.pages) > 1 else "")
        item = canvas.create_text(width / 2, int(height * 0.09), text=title, fill=faded(self.accent, self.level),
                                  font=("Georgia", -max(14, height // 18), "bold"))
        self.items.append((item, self.accent))
        texts = self.pages[self.page]
        if len(texts) == 2:
            room, middles, colors = int(height * 0.40), (int(height * 0.35), int(height * 0.78)), ("#ffffff", SECOND_INK)
            rule = canvas.create_line(width * 0.3, height * 0.565, width * 0.7, height * 0.565,
                                      fill=faded(self.accent, self.level))
            self.items.append((rule, self.accent))
        else:
            room, middles, colors = int(height * 0.76), (int(height * 0.55),), ("#ffffff",)
        top = int(max(30, height // (13 if len(texts) == 2 else 9)) * self.scale)
        sizes = [self._body_size()] if self.paged else list(range(top, 11, -2))  # largest size where everything fits
        for size in sizes:
            drawn = [canvas.create_text(width / 2, middle, text=text, fill=faded(color, self.level), justify="center",
                                        width=width - 2 * int(width * 0.07), font=("Georgia", -size))
                     for text, middle, color in zip(texts, middles, colors)]
            boxes = [canvas.bbox(item) for item in drawn]
            if all(box[3] - box[1] <= room for box in boxes) or size == sizes[-1]:
                break
            for item in drawn:
                canvas.delete(item)
        self.items.extend(zip(drawn, colors))
        self.last_change = time.monotonic()

    def _fade(self, target, duration, then=None):
        if self.fade_job:
            self.canvas.after_cancel(self.fade_job)
        start, frames = self.level, max(1, duration // self.FRAME)

        def step(frame):
            self.level = start + (target - start) * frame / frames
            for item, color in self.items:
                self.canvas.itemconfig(item, fill=faded(color, self.level))
            if self.image_item:
                self.canvas.itemconfig(self.image_item, image=self._frame())
            if self.mark_item and self.mark_frames:
                self.canvas.itemconfig(self.mark_item, image=self._mark_frame())
            if frame < frames:
                self.fade_job = self.canvas.after(self.FRAME, step, frame + 1)
            else:
                self.fade_job = None
                if then:
                    then()

        step(1)

    def _schedule_page(self):
        self._cancel_page()
        if self.page + 1 < len(self.pages):
            words = max(len(text.split()) for text in self.pages[self.page])  # each person reads one language
            self.page_job = self.canvas.after(int((words / self.WORDS_PER_SECOND + 1.5) * 1000), self._next_page)

    def _cancel_page(self):
        if self.page_job:
            self.canvas.after_cancel(self.page_job)
            self.page_job = None

    def _next_page(self):
        self.page_job = None

        def show_next():
            self.page += 1
            self._draw()
            self._fade(1, self.FADE_IN)
            self._schedule_page()

        self._fade(0, self.FADE_SWAP, show_next)


class App(MediaControl, SermonTestControl, ScreensControl, ChurchControl):
    def __init__(self, args):
        self.args = args
        self.settings = load_settings()
        self.events = queue.Queue()
        self.library = Library()
        self.finder = ReferenceFinder(self.library)
        default = self.settings.get("translation", "KJV")
        if default not in self.library.codes:  # a Bible that was removed since: fall back, or no verse could show
            default = self.library.codes[0]
        self.detector = Detector(self.library, self.finder, default=default)
        self.ring = RingBuffer()
        self.source = self.listener = self.transcriber = None
        if not self.settings.get("phone_mic_token"):
            self.settings["phone_mic_token"] = new_token()  # kept, so a phone's saved page keeps working
        self.phone = PhoneMic(self.ring, self.settings["phone_mic_token"])
        self.phone_was_active = False
        self.audio_checks_lost = 0
        self.last_health = time.monotonic()
        self.peak = 0.0  # loudest recent sound, for the ready check
        self.last_heard = None
        self.engine_error = None
        self.ready_window = None
        self.phone_window = None
        self.preview_ref = self.live_ref = None
        self.undo_stack, self.redo_stack = [], []
        self.preview_kind = None
        self.preview_code = self.live_code = self.detector.default
        saved_second = self.settings.get("second_translation", "")
        self.second_code = saved_second if saved_second in self.library.codes else ""
        self.history = []
        self.live_touched = self.live_shown = 0.0
        self.clear_after = max(0.0, float(self.settings.get("auto_clear_seconds", AUTO_CLEAR_SECONDS)))
        phrases = self.settings.get("clear_phrases", CLEAR_PHRASES)
        self.clear_pattern = re.compile(r"\b(" + "|".join(map(re.escape, phrases)) + r")\b") if phrases else None

        self.root = tk.Tk()
        self.root.report_callback_exception = self._log_ui_error
        self.root.title("Scripture Screen - Operator")
        self.root.geometry("1120x700+30+30")
        self.root.minsize(860, 560)
        self.root.configure(bg=BG)
        self._style()
        self.projector = Projector(self.root, self.settings.get("standby_text", STANDBY_TEXT))
        self.mode, self.live_media = "standby", None
        self.player, self.player_window, self.player_error = None, None, None
        self.video_item = None  # what is loaded in the player (on screen or getting ready)
        self.streams = {}  # media id -> the stream found behind its link this session
        self.finding = set()  # media ids whose link is being looked up
        self.retried = set()
        self.media, self.media_error = None, None
        try:  # a damaged media library must never stop the verses
            self.media = MediaLibrary()
        except Exception as error:
            logging.exception("Media library could not open")
            self.media_error = f"Media library could not open: {error}"
        self._init_screens()
        self._use_standby_image(self.settings.get("standby_image"))
        try:  # the church's own name, colour and logo; a bad logo file must never stop the verses
            self.apply_church()
        except Exception:
            logging.exception("Our Church settings could not be applied")
        self._init_service()
        self._init_sermon_test()
        self._build()
        self._refresh_displays()
        self._bind_keys()
        self.root.after(DISPLAY_CHECK_MS, self._watch_displays)
        if not self.phone.start():
            self.phone_status.config(text="Phone mic off", fg=RED)
        self._save_settings()
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        threading.Thread(target=self._load_models, daemon=True).start()
        self.root.after(80, self._tick)
        self.root.after(VIDEO_CHECK_MS, self._watch_video)

    def _style(self):
        base = tkfont.nametofont("TkDefaultFont").actual("family")
        self.font = lambda size, weight="normal": (base, size, weight)
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TCombobox", fieldbackground=FIELD, background=FIELD, foreground=INK, arrowcolor=INK)
        style.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", INK)])
        self.root.option_add("*TCombobox*Listbox.background", FIELD)
        self.root.option_add("*TCombobox*Listbox.foreground", INK)
        style.configure("Level.Horizontal.TProgressbar", troughcolor=FIELD, background=GREEN, borderwidth=0)

    def _label(self, parent, text="", size=10, color=MUTED, weight="normal", **kw):
        return tk.Label(parent, text=text, bg=parent["bg"], fg=color, font=self.font(size, weight), **kw)

    def _button(self, parent, text, color, command, width=14):
        return tk.Button(parent, text=text, bg=color, fg="white", activebackground=color, activeforeground="white",
                         relief="flat", bd=0, font=self.font(12, "bold"), width=width, pady=10, command=command,
                         takefocus=0, cursor="hand2")

    def _combo(self, parent, width, on_change):
        box = ttk.Combobox(parent, state="readonly", width=width, takefocus=0)
        box.bind("<<ComboboxSelected>>", on_change)
        return box

    def _build(self):
        top = tk.Frame(self.root, bg=PANEL, padx=12, pady=10)
        top.pack(fill="x")
        self.auto = tk.BooleanVar(value=bool(self.settings.get("auto", True)))
        tk.Checkbutton(top, text="AUTO", variable=self.auto, bg=PANEL, fg=GOLD, selectcolor=FIELD, activebackground=PANEL,
                       activeforeground=GOLD, font=self.font(11, "bold"), takefocus=0,
                       command=self._auto_changed).pack(side="left", padx=(0, 12))
        self._label(top, "Sound input").pack(side="left")
        self.mic_box = self._combo(top, 26, lambda _e: self._start_audio(remember=True))
        self.mic_box.pack(side="left", padx=(6, 6))
        self.level = ttk.Progressbar(top, style="Level.Horizontal.TProgressbar", length=90, maximum=100)
        self.level.pack(side="left", padx=(0, 10))
        tk.Button(top, text="PHONE MIC", command=self.show_phone_mic, bg=BLUE, fg="white", activebackground=BLUE,
                  activeforeground="white", relief="flat", bd=0, padx=10, pady=4, font=self.font(10, "bold"),
                  takefocus=0, cursor="hand2").pack(side="left")
        self.phone_status = self._label(top, "", 10, MUTED, "bold")
        self.phone_status.pack(side="left", padx=(6, 16))
        self._label(top, "Projector").pack(side="left")
        self.display_box = self._combo(top, 26, lambda _e: self._display_picked())
        self.display_box.pack(side="left", padx=6)
        tk.Button(top, text="Rescan", command=self._refresh_displays, bg=FIELD, fg=INK, relief="flat", bd=0, padx=8,
                  takefocus=0).pack(side="left")
        self.translation_box = self._combo(top, 7, lambda _e: self._set_translation(self.translation_box.get()))
        self.translation_box["values"] = self.library.codes
        self.translation_box.set(self.detector.default)
        self.translation_box.pack(side="right")
        self._label(top, "Default Bible").pack(side="right", padx=6)

        self._build_live_strip()
        self.pages = {name: tk.Frame(self.root, bg=BG) for name in ("scripture", "media", "order")}
        page = self.pages["scripture"]
        main = tk.Frame(page, bg=BG, padx=12, pady=10)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=2, uniform="c")
        main.columnconfigure(1, weight=3, uniform="c")
        main.rowconfigure(1, weight=1)

        left = tk.Frame(main, bg=BG)
        left.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, 10))
        heading = tk.Frame(left, bg=BG)  # the top bar is full, so the ready check sits here where there is room
        heading.pack(fill="x")
        self._label(heading, "HEARING NOW", 9, weight="bold").pack(side="left")
        tk.Button(heading, text="READY FOR SERVICE? (F5)", command=self.show_ready, bg=GREEN, fg="white",
                  activebackground=GREEN, activeforeground="white", relief="flat", bd=0, padx=8, pady=1,
                  font=self.font(9, "bold"), takefocus=0, cursor="hand2").pack(side="right")
        tk.Button(heading, text="SERMON TEST", command=self.show_sermon_test, bg=FIELD, fg=INK, activebackground=FIELD,
                  activeforeground=INK, relief="flat", bd=0, padx=8, pady=1, font=self.font(9, "bold"), takefocus=0,
                  cursor="hand2").pack(side="right", padx=(0, 6))
        self.transcript = self._label(left, "Loading speech engine...", 12, INK, wraplength=400, justify="left",
                                      anchor="nw", height=6)
        self.transcript.pack(fill="x", pady=(2, 10))
        self._label(left, "DETECTED (click to preview)", 9, weight="bold").pack(anchor="w")
        self.history_list = tk.Listbox(left, bg=PANEL, fg=INK, selectbackground=BLUE, relief="flat", bd=0,
                                       highlightthickness=0, font=self.font(12), activestyle="none", takefocus=0)
        self.history_list.pack(fill="both", expand=True, pady=(2, 0))
        self.history_list.bind("<<ListboxSelect>>", self._pick_history)

        search = tk.Frame(main, bg=BG)
        search.grid(row=0, column=1, sticky="ew")
        self.second_box = self._combo(search, 7, lambda _e: self._set_second(self.second_box.get()))
        self.second_box["values"] = [NO_SECOND, *self.library.codes]
        self.second_box.set(self.second_code or NO_SECOND)
        self.second_box.pack(side="right")
        self._label(search, "Also show").pack(side="right", padx=6)
        self._label(search, "Type a verse").pack(side="left")
        self.search_entry = tk.Entry(search, bg=FIELD, fg=INK, insertbackground=INK, relief="flat",
                                     font=self.font(13))
        self.search_entry.pack(side="left", fill="x", expand=True, padx=8, ipady=5)
        self.search_entry.bind("<Return>", self._typed_search)
        # Book names drop down as the operator types ("re" -> Revelation), so a wrong verse is fixed in moments.
        self.book_list = tk.Listbox(self.root, bg=PANEL, fg=INK, selectbackground=BLUE, relief="flat", bd=1,
                                    highlightthickness=1, highlightbackground=BLUE, font=self.font(13),
                                    activestyle="none", takefocus=0, exportselection=False, height=8)
        self.book_choices = []
        self.book_list.bind("<ButtonRelease-1>", lambda _e: self._accept_book())
        self.search_entry.bind("<KeyRelease>", self._typing_verse)
        self.search_entry.bind("<Down>", lambda _e: self._move_book(1))
        self.search_entry.bind("<Up>", lambda _e: self._move_book(-1))
        self.search_entry.bind("<Tab>", lambda _e: self._accept_book() if self._books_shown() else None)
        self.search_entry.bind("<Escape>", self._entry_escape)
        self.search_entry.bind("<FocusOut>", lambda _e: self.root.after(150, self._hide_books))

        card = tk.Frame(main, bg=PANEL, padx=18, pady=14)
        card.grid(row=1, column=1, sticky="nsew", pady=(10, 0))
        # The panel takes the room the window gives it and never asks for more. Without this, a verse whose length
        # sat exactly on the wrap edge made panel and text resize each other for ever, freezing the operator window.
        card.pack_propagate(False)
        self.alert = self._label(card, "PREVIEW", 9, BLUE, "bold")
        self.alert.pack(anchor="w")
        self.preview_title = self._label(card, "No verse yet", 20, GOLD, "bold")
        self.preview_title.pack(anchor="w", pady=(4, 8))
        self.preview_body = self._label(card, "", 15, INK, wraplength=600, justify="left", anchor="nw")
        self.preview_body.pack(fill="both", expand=True)
        card.bind("<Configure>", lambda e: self.preview_body.config(wraplength=e.width - 40))

        bottom = tk.Frame(page, bg=PANEL, padx=12, pady=10)
        bottom.pack(fill="x")
        self._button(bottom, "◀ Verse", FIELD, lambda: self._step(-1), 9).pack(side="left", padx=(0, 6))
        self._button(bottom, "Verse ▶", FIELD, lambda: self._step(1), 9).pack(side="left")
        self._button(bottom, "Undo", FIELD, self.undo_live, 6).pack(side="left", padx=(16, 6))
        self._button(bottom, "Redo", FIELD, self.redo_live, 6).pack(side="left")
        self.status = self._label(bottom, "", 10)
        self.status.pack(side="left", padx=16)
        self._button(bottom, "CLEAR (Esc)", RED, self.clear_live).pack(side="right")
        self._button(bottom, "WRONG VERSE (F2)", GOLD, self.wrong_verse, 17).pack(side="right", padx=(8, 0))
        self._button(bottom, "PUSH LIVE (Space)", GREEN, self.push_live, 18).pack(side="right", padx=8)
        self._build_media_page(self.pages["media"])
        self._build_order_page(self.pages["order"])
        self.show_page("scripture")

    def _bind_keys(self):
        def outside_entry(action):
            def handler(_event):
                if not self._typing():
                    action()
            return handler

        self.root.bind("<space>", outside_entry(self._space))
        self.root.bind("<Left>", outside_entry(lambda: self._arrow(-1)))
        self.root.bind("<Right>", outside_entry(lambda: self._arrow(1)))
        self.root.bind("<Escape>", self._escape)
        self.root.bind("<F2>", lambda _e: self.wrong_verse())
        self.root.bind("<F5>", lambda _e: self.show_ready())
        for keys, action in (("zZ", self.undo_live), ("yY", self.redo_live)):
            for key in keys:
                self.root.bind(f"<Control-{key}>", outside_entry(action))
        self.root.bind("<Button-1>", self._click_away, add="+")

    def _typing(self):
        return isinstance(self.root.focus_get(), (tk.Entry, tk.Text))

    def _click_away(self, event):
        if not isinstance(event.widget, (tk.Entry, tk.Text)):
            self.root.focus_set()

    def _space(self):
        """Space pushes whatever the open page is previewing."""
        if self.page == "media":
            self.push_media()
        elif self.page == "order":
            self.go_order()
        else:
            self.push_live()

    def _arrow(self, delta):
        """Left/Right: the next stanza while a hymn is up (away from the Bible page), else the next verse."""
        if self.mode == "hymn" and self.page != "scripture":
            self.hymn_step(delta)
        else:
            self._step(delta)

    def _escape(self, _event):
        if self._typing():
            self.root.focus_set()
        else:
            self.clear_live()

    def _refresh_displays(self):
        self.displays = list_displays(self.root)
        self.display_box["values"] = [d.label for d in self.displays]
        saved = self.settings.get("display")
        external = next((d for d in self.displays if not d.primary), self.displays[0])
        chosen = next((d for d in self.displays if d.label == saved), external)
        self.display_box.set(chosen.label)
        self._place_projector()

    def _place_projector(self):
        chosen = next(d for d in self.displays if d.label == self.display_box.get())
        self.projector.place(chosen, is_only_screen=len(self.displays) == 1)
        try:
            self._place_second()
        except Exception:
            logging.exception("Second screen could not be placed")
        self.root.focus_force()

    def _display_picked(self):
        self._place_projector()
        self.settings["display"] = self.display_box.get()  # only a person's choice is remembered
        self._save_settings()

    def _watch_displays(self):
        """Follow HDMI being plugged in or pulled out during a service."""
        try:
            if list_displays(self.root) != self.displays:
                logging.info("Screens changed; moving the projector window")
                self._refresh_displays()
        except Exception:
            logging.exception("Screen check failed")
        self.root.after(DISPLAY_CHECK_MS, self._watch_displays)

    # ---- speech ----
    def _load_models(self):
        try:
            self.transcriber = Transcriber(self.args.model or self.settings.get("model", "tiny.en"),
                                           min_confidence=min(95, max(0, self.settings.get("min_confidence", 50))) / 100)
        except Exception as error:
            self.engine_error = f"Speech engine failed: {error}"
            self.events.put(("error", self.engine_error))
            return
        self.events.put(("speech_ready", None))
        try:
            from .semantic import SemanticIndex

            self.detector.semantic = SemanticIndex(self.library)
            self.events.put(("status", "Paraphrase search ready."))
        except Exception as error:
            self.events.put(("status", f"Paraphrase search off: {error}"))

    def _speech_ready(self):
        self.mics = list_microphones()
        self.mic_box["values"] = [name for _i, name in self.mics]
        saved = self.settings.get("microphone")
        names = [name for _i, name in self.mics]
        if saved in names:
            self.mic_box.set(saved)
        elif names:
            self.mic_box.set(names[0])
        self.listener = Listener(self.ring, self.transcriber, self._on_text)
        self.listener.start()
        self._start_audio(remember=False)
        self.root.after(HEALTH_CHECK_MS, self._watch_audio)

    def _start_audio(self, remember=False):
        if getattr(self, "sermon_feeder", None):  # a sermon test is speaking; stop_sermon_test brings the mic back
            return
        if self.source:
            self.source.stop()
            self.source = None
        if self.args.simulate:
            self.source = FilePlayer(self.ring, self.args.simulate)
            self.transcript.config(text="Playing test audio...")
            return
        index = next((i for i, name in self.mics if name == self.mic_box.get()), None)
        if index is None:
            self.transcript.config(text="No sound input found. Plug one in; you can still type verses.")
            return
        try:
            self.source = Microphone(self.ring, index, muted=self.phone.active)
            if remember:
                self.settings["microphone"] = self.mic_box.get()
                self._save_settings()
            self.transcript.config(text="Listening...")
        except Exception as error:
            logging.exception("Could not open sound input")
            self.transcript.config(text=f"Could not open this input: {error}")

    def _watch_audio(self):
        """Reconnect by itself if the mixer cable or USB adapter is pulled out, and restart listening if it stalls."""
        try:
            now = time.monotonic()
            gap, self.last_health = now - self.last_health, now
            if gap > ASLEEP_SECONDS:
                logging.warning("The laptop was asleep or frozen for %d seconds; carrying on", gap)
                self.status.config(text=f"The laptop was asleep for {max(1, round(gap / 60))} min. Keep the lid open.")
                if self.listener:
                    self.listener.beat = now  # it was asleep too, not stuck: no need to restart it
        except Exception:
            logging.exception("Sleep check failed")
        try:
            if self.source and self.source.alive():
                self.audio_checks_lost = 0
            else:
                self.audio_checks_lost += 1
                # Re-reading the sound hardware briefly holds up the screen, so after the first tries do it less often.
                if self.audio_checks_lost <= 3 or self.audio_checks_lost % 3 == 0:
                    self._recover_audio()
        except Exception:
            logging.exception("Sound input recovery failed")
        try:
            if self.listener and self.listener.stalled():
                logging.error("Listening stalled; restarting it")
                self.listener.stop()
                self.listener = Listener(self.ring, self.transcriber, self._on_text)
                self.listener.start()
                self.status.config(text="Listening restarted.")
        except Exception:
            logging.exception("Could not restart listening")
        self.root.after(HEALTH_CHECK_MS, self._watch_audio)

    def _recover_audio(self):
        if self.audio_checks_lost <= 1:  # once per outage, not every three seconds
            logging.warning("Sound input lost; reconnecting")
        if self.source:
            self.source.stop()
            self.source = None
        self.mics = list_microphones(refresh=True)
        names = [name for _i, name in self.mics]
        self.mic_box["values"] = names
        wanted = self.settings.get("microphone")
        if wanted in names:
            self.mic_box.set(wanted)
        elif names and self.mic_box.get() not in names:
            self.mic_box.set(names[0])  # keep hearing the pastor through whatever is available
        self._start_audio(remember=False)
        if self.source:
            logging.info("Sound input reconnected: %s", self.mic_box.get())
            self.status.config(text=f"Sound input reconnected: {self.mic_box.get()}")

    def _on_text(self, text, seconds):  # worker thread: only queue, never touch widgets
        events = self.detector.process(text, time.monotonic(), seconds)
        self.events.put(("transcript", (text, seconds, events)))

    def _tick(self):
        """Must always reschedule itself: if this loop dies, the app stops following the pastor with no visible sign."""
        while True:
            try:
                name, value = self.events.get_nowait()
            except queue.Empty:
                break
            try:
                self._handle_event(name, value)
            except Exception:
                logging.exception("Could not apply a %s event", name)
        try:
            self.level["value"] = min(100, self.ring.level * 400)
            self.peak = max(getattr(self, "peak", 0.0) * 0.97, self.ring.level)
            self._phone_changed()
            for found in self.detector.flush(time.monotonic()).found:
                logging.info("%sHeard once, nothing against it -> %s", self._sermon_mark(), found.ref.label)
                self._add_detection(found)
            self._auto_expiry()
        except Exception:
            logging.exception("Screen update failed")
        self.root.after(80, self._tick)

    def _handle_event(self, name, value):
        if name == "speech_ready":
            self._speech_ready()
        elif name == "transcript":
            self._apply(*value)
        elif name == "status":
            self.status.config(text=value)
        elif name == "error":
            self.transcript.config(text=value + " You can still type verses.")
        elif name in ("sermon_stream", "sermon_failed"):
            self._sermon_event(name, value)
        elif name in ("stream_found", "stream_failed", "stream_thumb", "video_copied", "copy_failed"):
            self._media_event(name, value)

    def _apply(self, text, seconds, events):
        self.transcript.config(text=text)
        if text:
            self.last_heard = time.monotonic()
            logging.info("%sHeard: %s -> %s", self._sermon_mark(), text,
                         ", ".join(f.ref.label for f in events.found) or "-")
        hearing = self.transcriber.hearing
        self.status.config(text=f"Speech {seconds:.1f}s" + (f", hearing {hearing:.0%}" if hearing is not None else ""))
        self._hear_while_live(text)
        if events.translation:
            self._switch_current(events.translation, "voice")
        for found in events.found:
            self._add_detection(found)
        if events.missing_translation:
            self.alert.config(text=f"{events.missing_translation} REQUESTED BUT NOT INSTALLED", fg=RED)

    def _add_detection(self, found):
        self.history.insert(0, (found.ref, found.kind, found.translation))
        extra = f" {found.score:.0%}" if found.score is not None else ""
        self.history_list.insert(0, f"{found.ref.label}   ({KIND_TAGS[found.kind]}{extra})")
        if len(self.history) > MAX_HISTORY:
            self.history.pop()
            self.history_list.delete("end")
        grows = self._completes_live(found.ref)
        if found.triggered or grows:
            self._set_preview(found.ref, found.kind, found.translation if not grows else self.live_code)
            if self.mode in PROTECTED_MODES and found.kind != "typed":
                self._verse_while_media(found)
            elif self.auto.get():
                self.push_live()
        else:  # never replace what the pastor asked for; the operator can click it in the list
            self.alert.config(text=f"SUGGESTED: {found.ref.label} (CLICK IT IN THE LIST)", fg=GOLD)

    def _verse_while_media(self, found):
        """Hymn, choir or announcement time: talk in the room must not knock the poster off the screen. A verse the
        pastor asks for outright ("media, give me John 3:16") goes up at once only if settings say "switch"."""
        asked = found.kind == "command"
        if asked and self.auto.get() and self.settings.get("verse_during_media", "ask") == "switch":
            self.push_live()
            return
        what = "PASTOR ASKED FOR" if asked else "HEARD"
        self.alert.config(text=f"MEDIA IS ON SCREEN. {what} {found.ref.label}: PRESS SPACE TO SHOW IT", fg=GOLD)
        if asked:
            self.show_page("scripture")
            self.live_note.config(text=f"Pastor asked for {found.ref.label}: Space shows it")

    # ---- phone mic ----
    def _phone_changed(self):
        active = self.phone.active()
        if active == self.phone_was_active:
            return
        self.phone_was_active = active
        if active:
            logging.info("Phone mic connected")
            self.phone_status.config(text="● PHONE MIC ON", fg=GREEN)
            self.transcript.config(text="Listening through the phone...")
        else:
            logging.warning("Phone mic stopped; back to the laptop's sound input")
            self.phone_status.config(text="Phone lost: using laptop mic", fg=GOLD)
        if self.phone_window:
            self._fill_phone_window()

    def show_phone_mic(self):
        if self.phone_window and self.phone_window.winfo_exists():
            self._fill_phone_window()
            self.phone_window.lift()
            return
        self.phone_window = tk.Toplevel(self.root, bg=BG, padx=20, pady=16)
        self.phone_window.title("Phone mic")
        self.phone_window.transient(self.root)
        self._fill_phone_window()

    def _fill_phone_window(self):
        window = self.phone_window
        if not window or not window.winfo_exists():
            return
        for child in window.winfo_children():
            child.destroy()
        urls = self.phone.urls()
        if self.phone.active():
            self._label(window, "● PHONE CONNECTED, LISTENING", 16, GREEN, "bold").pack(pady=(0, 10))
        if not self.phone.port:
            self._label(window, self.phone.error or "Phone mic is not running.", 13, RED, "bold").pack()
            return
        if not urls:
            self._label(window, "No network yet.", 15, GOLD, "bold").pack(anchor="w")
            self._label(window, "Turn on the phone's hotspot and connect this laptop to it,\n"
                        "or turn on this laptop's Mobile hotspot. Then press Refresh.", 12, INK, justify="left").pack(
                anchor="w", pady=(4, 10))
        else:
            self._label(window, "Scan with the phone camera, then tap START", 15, INK, "bold").pack()
            image = ImageTk.PhotoImage(Image.open(io.BytesIO(qr_png(urls[0]))))
            holder = tk.Label(window, image=image, bg="white")
            holder.image = image
            holder.pack(pady=10)
            self._label(window, urls[0], 11, MUTED).pack()
            self._label(window, "Phone and laptop must be on the same hotspot. The first time, the phone shows a\n"
                        "security warning: tap Advanced, then Continue/Proceed. It is safe, it is your own laptop.",
                        10, MUTED, justify="center").pack(pady=(8, 4))
            if len(urls) > 1:
                self._label(window, "If it does not open, try: " + "   ".join(urls[1:3]), 9, MUTED,
                            wraplength=520).pack()
        tk.Button(window, text="Refresh", command=self._fill_phone_window, bg=FIELD, fg=INK, relief="flat", bd=0,
                  padx=14, pady=6, takefocus=0).pack(pady=(10, 0))

    # ---- ready check ----
    def ready_lines(self):
        plugged, battery = power_status()
        listening = bool(self.listener and self.listener.thread and self.listener.thread.is_alive())
        has_input = bool(self.source and self.source.alive())
        return ready.check(
            engine_loaded=self.transcriber is not None, engine_error=self.engine_error, listening=listening,
            input_name=(self.mic_box.get() or "the sound input") if has_input else "", phone_on=self.phone.active(),
            phone_running=bool(self.phone.port), peak=self.peak,
            heard_ago=None if self.last_heard is None else time.monotonic() - self.last_heard,
            screens=len(self.displays), screen_name=self.display_box.get(), bible=self.detector.default,
            bible_ok=bool(self.library.text(self.detector.default, Ref("John", 3, 16))), second=self.second_code,
            plugged=plugged, battery=battery, free_mb=free_megabytes(), auto=bool(self.auto.get()))

    def show_ready(self):
        """Before the service: one window that says, in plain words, whether everything is working."""
        if self.ready_window and self.ready_window.winfo_exists():
            self.ready_window.lift()
            return
        window = self.ready_window = tk.Toplevel(self.root, bg=BG, padx=22, pady=18)
        window.title("Ready for service?")
        window.transient(self.root)
        self.ready_head = self._label(window, "", 18, INK, "bold")
        self.ready_head.pack(anchor="w", pady=(0, 12))
        self.ready_rows = tk.Frame(window, bg=BG)
        self.ready_rows.pack(fill="both", expand=True)
        buttons = tk.Frame(window, bg=BG)
        buttons.pack(fill="x", pady=(14, 0))
        for text, color, command in (("Show a test verse on the TV", BLUE, self._ready_test_verse),
                                     ("Take it off", FIELD, self.clear_live), ("Close", FIELD, window.destroy)):
            tk.Button(buttons, text=text, command=command, bg=color, fg="white", activebackground=color,
                      activeforeground="white", relief="flat", bd=0, padx=14, pady=8, font=self.font(11, "bold"),
                      takefocus=0, cursor="hand2").pack(side="left", padx=(0, 8))
        self._refresh_ready()

    def _ready_test_verse(self):
        self._add_detection(Found(Ref("John", 3, 16), "typed", translation=self.detector.default, triggered=True))
        if not self.auto.get():
            self.push_live()

    def _refresh_ready(self):
        window = self.ready_window
        if not window or not window.winfo_exists():
            return
        try:
            lines = self.ready_lines()
            state, words = ready.verdict(lines)
            self.ready_head.config(text=words, fg=READY_COLORS[state])
            for child in self.ready_rows.winfo_children():
                child.destroy()
            for row, line in enumerate(lines):
                tk.Label(self.ready_rows, text="●", bg=BG, fg=READY_COLORS[line.state],
                         font=self.font(14)).grid(row=row, column=0, sticky="w", padx=(0, 8))
                self._label(self.ready_rows, line.name, 12, INK, "bold").grid(row=row, column=1, sticky="w", padx=(0, 14))
                self._label(self.ready_rows, line.detail, 12, MUTED, wraplength=520, justify="left").grid(
                    row=row, column=2, sticky="w", pady=3)
        except Exception:
            logging.exception("Ready check failed")
        window.after(1000, self._refresh_ready)

    def wrong_verse(self):
        """One press: the wrong verse leaves the TV (back to what was there before), and the typing box opens with the
        book and chapter already filled in, so the operator types only the right verse number and presses Enter."""
        wrong = self.live_ref or self.preview_ref
        if self.live_ref:
            if self.undo_stack:
                self.undo_live()
            else:
                self.clear_live()
        self.search_entry.delete(0, "end")
        if wrong:
            self.search_entry.insert(0, f"{wrong.book} {wrong.chapter} ")
        self.search_entry.focus_set()
        self.search_entry.icursor("end")
        if wrong:
            self._refresh_choices()  # that chapter's verses, with their words: click the right one
        self.alert.config(text="TYPE THE RIGHT VERSE NUMBER (OR CLICK IT), THEN PRESS ENTER", fg=GOLD)

    def _completes_live(self, ref):
        """'Revelation 3 verse 1...' went live because the window cut the pastor off mid-number; when '...18' arrives
        moments later, swap it in, even with no 'media' in front of it this time."""
        live = self.live_ref
        if not live or time.monotonic() - self.live_shown > COMPLETE_SECONDS:
            return False
        return (ref.book, ref.chapter) == (live.book, live.chapter) and ref.verse != live.verse \
            and str(ref.verse).startswith(str(live.verse))

    def _pick_history(self, _event):
        selected = self.history_list.curselection()
        if selected:
            ref, kind, code = self.history[selected[0]]
            self._set_preview(ref, kind, code)
            self.detector.set_context(ref)

    # ---- book names while typing ----
    def _books_shown(self):
        return bool(self.book_list.winfo_ismapped())

    def _typing_verse(self, event):
        if event.keysym in ("Up", "Down", "Return", "KP_Enter", "Tab", "Escape"):
            return
        self._refresh_choices()
        text = self.search_entry.get()
        if book_part(text) is None:
            self._preview_typed(text)

    def _refresh_choices(self):
        """Books, then that book's chapters, then that chapter's verses with their first words (owner 2026-11-17)."""
        code = self.detector.default
        try:
            self.book_choices = suggest(self.search_entry.get(), self.library.shape,
                                        lambda b, c, v: self.library.text(code, Ref(b, c, v)))
        except Exception:
            logging.exception("Verse suggestions failed")
            self.book_choices = []
        if not self.book_choices:
            self._hide_books()
            return
        self.book_list.delete(0, "end")
        for shown, _fill, _kind in self.book_choices:
            self.book_list.insert("end", shown)
        self.book_list.config(height=min(10, len(self.book_choices)))
        self.book_list.selection_set(0)
        self.book_list.see(0)
        # As wide as the room to the window's right edge, so the verse's first words can be read.
        room = self.root.winfo_rootx() + self.root.winfo_width() - self.search_entry.winfo_rootx() - 16
        self.book_list.place(in_=self.search_entry, x=0, rely=1, y=2, width=max(self.search_entry.winfo_width(), room))
        self.book_list.lift()

    def _preview_typed(self, text):
        """As soon as the reference is complete, the verse shows in PREVIEW; Enter puts it on screen."""
        typed = re.sub(r"(?<=[A-Za-z])\.", " ", text)
        if not re.search(r"\d", typed):
            return
        try:
            matches = self.finder.find(typed, typed=True)
        except Exception:
            return
        if matches:
            self._set_preview(matches[0].ref, "typed", self.detector.default)
            self.alert.config(text="PRESS ENTER TO PUT IT ON SCREEN", fg=GREEN)

    def _move_book(self, step):
        if not self._books_shown():
            return None
        chosen = self.book_list.curselection()
        row = max(0, min(self.book_list.size() - 1, (chosen[0] if chosen else -1) + step))
        self.book_list.selection_clear(0, "end")
        self.book_list.selection_set(row)
        self.book_list.see(row)
        return "break"

    def _accept_book(self):
        chosen = self.book_list.curselection()
        if not chosen:
            return "break"
        _shown, fill, kind = self.book_choices[chosen[0]]
        self.search_entry.delete(0, "end")
        self.search_entry.insert(0, fill)
        self.search_entry.icursor("end")
        self.search_entry.focus_set()
        if kind == "verse":  # the verse itself: straight on screen, as Enter on a typed verse does
            self._hide_books()
            self._typed_search(None)
        else:  # a book or chapter: the next list (its chapters, or its verses) opens at once
            self._refresh_choices()
        return "break"

    def _hide_books(self):
        try:
            self.book_list.place_forget()
        except tk.TclError:
            pass

    def _entry_escape(self, _event):
        if self._books_shown():
            self._hide_books()
            return "break"
        return None

    def _typed_search(self, event):
        if event is not None and self._books_shown():
            return self._accept_book()  # Enter takes the highlighted book, chapter or verse
        self._hide_books()
        typed = re.sub(r"(?<=[A-Za-z])\.", " ", self.search_entry.get())  # "Rev. 3 18", "Gen. 1:1"
        matches = self.finder.find(typed, typed=True)
        if not matches:
            self.alert.config(text="COULD NOT READ THAT REFERENCE, TRY: JOHN 3 16", fg=RED)
            return
        self._add_detection(Found(matches[0].ref, "typed", translation=self.detector.default, triggered=True))
        self.detector.set_context(matches[0].ref)
        self.search_entry.delete(0, "end")
        self.root.focus_set()

    # ---- preview and live ----
    def _verse(self, ref, code):
        return f"{ref.label} ({code})", self.library.text(code, ref)

    def _set_preview(self, ref, kind, code):
        passage = ref if ref.end_verse and ref.end_verse > ref.verse else None
        if passage:  # owner 2026-11-11: one full verse at a time; "next verse" / "verse 18" walks the passage
            ref = Ref(ref.book, ref.chapter, ref.verse)
        self.preview_ref, self.preview_kind, self.preview_code = ref, kind, code
        title, body = self._verse(ref, code)
        self.preview_title.config(text=title)
        self.preview_body.config(text=body or "This verse is not in the selected translation.")
        self.alert.config(text=f"PREVIEW: {KIND_TAGS[kind].upper()}",
                          fg={"paraphrase": GOLD, "book_guess": GOLD, "command": GREEN}.get(kind, BLUE))
        if passage:
            self.alert.config(text=f"PASSAGE {passage.label}: SHOWING VERSE {ref.verse}, THEN NEXT VERSE / ▶")
        self._update_next()

    def _step(self, delta):
        if self.preview_ref:
            ref = self.library.step(self.preview_ref, delta)
            self._set_preview(ref, "typed", self.preview_code)
            self.detector.set_context(ref)

    def push_live(self):
        if not self.preview_ref:
            return
        before = self._live_state()
        if self._project(self.preview_ref, self.preview_code):
            if before != (self.preview_ref, self.preview_code):
                self._remember(before)
            self.live_touched = self.live_shown = time.monotonic()
            self.live_ref, self.live_code = self.preview_ref, self.preview_code
            self.detector.set_context(self.live_ref)
            self.detector.current = self.live_code
            self.detector.note_shown(self.live_ref, self.live_code)
            self._update_next()

    # ---- undo and redo of what is on screen ----
    def _live_state(self):
        return (self.live_ref, self.live_code) if self.live_ref else None

    def _remember(self, state):
        """A new screen starts a new history going forward."""
        self.redo_stack.clear()
        if state:
            self.undo_stack = (self.undo_stack + [state])[-MAX_UNDO:]

    def undo_live(self):
        self._travel(self.undo_stack, self.redo_stack, "UNDO")

    def redo_live(self):
        self._travel(self.redo_stack, self.undo_stack, "REDO")

    def _travel(self, source, destination, name):
        if not source:
            self.alert.config(text=f"NOTHING TO {name}", fg=GOLD)
            return
        destination.append(self._live_state())
        target = source.pop()
        if target is None:  # the screen was on the logo at this point
            self.projector.clear()
            self.live_ref = None
            self._set_mode("standby")
            return
        ref, code = target
        self._set_preview(ref, "typed", code)
        if self._project(ref, code):
            self.live_touched = self.live_shown = time.monotonic()
            self.live_ref, self.live_code = ref, code
            self.detector.set_context(ref)
            self.detector.current = code
            self.detector.note_shown(ref, code)
            self._update_next()

    def _project(self, ref, code):
        """Put a verse on screen, with the 'Also show' language under it. A verse that version lacks shows alone."""
        title, body = self._verse(ref, code)
        if not body and code != "KJV":  # a verse this Bible leaves out: show the King James rather than nothing
            code = "KJV"
            title, body = self._verse(ref, code)
        if not body:
            return False
        second = None
        if self.second_code and self.second_code != code:
            second = self.library.text(self.second_code, ref)
        if second:
            title = f"{ref.label} ({code} / {self.second_code})"
        self.projector.show(title, body, second=second)  # never split a verse into pages (owner 2026-11-11)
        self._set_mode("scripture", title)
        logging.info("%sOn screen: %s", self._sermon_mark(), title)
        return True

    def _set_second(self, choice):
        self.second_code = "" if choice == NO_SECOND else choice
        self.settings["second_translation"] = self.second_code
        self._save_settings()
        if self.live_ref:
            self._project(self.live_ref, self.live_code)

    def clear_live(self):
        if self.live_ref:
            self._remember(self._live_state())
        self.projector.clear()
        self.live_ref = None
        self._set_mode("standby")

    def _auto_changed(self):
        self.settings["auto"] = self.auto.get()
        self._save_settings()

    def _auto_expiry(self):
        if self.clear_after > 0 and self.auto.get() and self.live_ref:
            latest = max(self.live_touched, self.projector.last_change)
            if time.monotonic() - latest > self.clear_after:
                self.clear_live()

    def _hear_while_live(self, text):
        """In Auto mode: clear on a closing phrase; stay up while the verse is being read aloud."""
        if not (self.auto.get() and self.live_ref):
            return
        spoken = text.lower()
        verse = (self.library.text(self.live_code, self.live_ref) or "").lower()
        if self.clear_pattern and time.monotonic() - self.live_shown >= MIN_SECONDS_LIVE:
            found = self.clear_pattern.search(spoken)
            if found and found[1] not in verse:
                self.clear_live()
                return
        shared = set(re.findall(r"[a-z]{5,}", spoken)) & set(re.findall(r"[a-z]{5,}", verse))
        if len(shared) >= 3:
            self.live_touched = time.monotonic()

    def _set_translation(self, code):
        """The dropdown sets the default used whenever the pastor names no version."""
        self.detector.default = self.detector.current = code
        self.settings["translation"] = code
        self._save_settings()
        self._switch_current(code, "operator")

    def _switch_current(self, code, source):
        if self.preview_ref:
            self._set_preview(self.preview_ref, self.preview_kind, code)
        if self.live_ref:  # what the congregation sees follows the pastor immediately
            if self._project(self.live_ref, code):
                self.live_touched = time.monotonic()
                self.live_code = code
        self.status.config(text=f"Bible switched to {code} by {source}.")

    def _save_settings(self):
        """Write to a temporary file first, so a power cut cannot leave a half-written settings file."""
        try:
            temporary = SETTINGS_PATH.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.settings, indent=2), encoding="utf-8")
            temporary.replace(SETTINGS_PATH)
        except OSError:
            logging.exception("Could not save settings")

    @staticmethod
    def _log_ui_error(*error):
        logging.error("Screen update failed", exc_info=error)

    def quit(self):
        self.stop_sermon_test(quiet=True)
        self._save_settings()
        if self.player:
            self.player.close()
        if self.media:
            self.media.close()
        self.phone.stop()
        self.outputs.stop()
        if self.listener:
            self.listener.stop()
        if self.source:
            self.source.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    parser = argparse.ArgumentParser(description="Live speech-to-scripture display")
    parser.add_argument("--model", help="tiny.en, base.en or small.en")
    parser.add_argument("--simulate", help="play a WAV file instead of using a microphone")
    args = parser.parse_args()
    setup_logging()
    lock = acquire_single_instance()
    if lock is None:
        show_fatal("Scripture Screen is already running.")
        return
    enable_windows_dpi_awareness()
    keep_laptop_awake()
    try:
        refresh_bibles()
        App(args).run()
    except Exception as error:
        logging.exception("Could not start")
        show_fatal(f"Scripture Screen could not start:\n\n{error}\n\nDetails are in logs/app.log.")
        os._exit(1)
    os._exit(0)  # the speech engine's native threads can abort a normal interpreter shutdown


_kept = []  # windows closed before the app starts; kept so Tk never tidies them up on a worker thread


def refresh_bibles():
    """A Bible file was added to data/bibles or my_bibles (or one was removed): build the database again, once.

    If that fails and an older database exists, the service goes on with the older one.
    """
    if not bibles_changed():
        return
    logging.info("Bible files changed; rebuilding the database")
    window = None
    try:
        window = tk.Tk()
        window.title("Scripture Screen")
        window.configure(bg=BG, padx=40, pady=30)
        tk.Label(window, text="Updating the Bibles...\nThis takes about a minute and happens once.", bg=BG, fg=INK,
                 font=("Segoe UI", 14), justify="center").pack()
        window.update()
    except Exception:
        logging.exception("Could not show the updating message")
    try:
        for note in build_bibles():
            logging.info("Bible build: %s", note)
    except Exception:
        logging.exception("Could not rebuild the Bible database; using the one from before")
    if window is not None:
        _kept.append(window)
        try:
            window.destroy()
        except Exception:
            pass


def show_fatal(message):
    window = tk.Tk()
    window.withdraw()
    messagebox.showerror("Scripture Screen", message)
    window.destroy()
