"""Media control (2026-11-17): announcements, pictures, videos and links on the one projector output.

Mixed into App: it uses the App's projector, settings, events queue and verse state.
"""
import io
import logging
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog
from tkinter import ttk

from PIL import Image, ImageTk

from .bible import ROOT
from .media import ACCEPTS, KINDS as MEDIA_KINDS
from .media import load_picture
from .style import BG, BLUE, FIELD, GOLD, GREEN, INK, MUTED, PANEL, RED
from . import video as video_module
from .video import SKIP_SECONDS, VideoError, fmt_time
from .service_page import ServiceControl

STANDBY_IMAGE = ROOT / "data" / "standby.jpg"
# While a poster, video or other media is up (or the operator blanked the screen), speech never replaces it.
PROTECTED_MODES = ("media", "video", "hymn", "black")
VIDEO_CHECK_MS = 400


def _app():
    from . import app  # late: app imports this module
    return app


class MediaControl(ServiceControl):
    # ---- what is on the projector: one output, shared by the Bible and the media pages ----
    def _build_live_strip(self):
        strip = tk.Frame(self.root, bg=BG, padx=12, pady=8)
        strip.pack(fill="x")
        self.tab_buttons = {}
        for name, text in (("scripture", "BIBLE / SCRIPTURE"), ("media", "MEDIA"), ("order", "SERVICE ORDER")):
            button = tk.Button(strip, text=text, command=lambda n=name: self.show_page(n), relief="flat", bd=0,
                               padx=14, pady=6, font=self.font(11, "bold"), takefocus=0, cursor="hand2")
            button.pack(side="left", padx=(0, 6))
            self.tab_buttons[name] = button
        self._button(strip, "BLACK SCREEN", FIELD, self.black_screen, 13).pack(side="right")
        self._button(strip, "SCREENS", BLUE, self.show_screens, 9).pack(side="right", padx=(0, 8))
        self._button(strip, "OUR CHURCH", FIELD, self.show_church, 11).pack(side="right", padx=(0, 8))
        self._button(strip, "TAKE OFF LIVE", RED, self.clear_live, 13).pack(side="right", padx=8)
        self.live_label = self._label(strip, "", 12, MUTED, "bold", anchor="w")
        self.live_label.pack(side="left", padx=(14, 8))
        self.live_note = self._label(strip, "", 10, GOLD, "bold", anchor="w")
        self.live_note.pack(side="left")
        self._set_mode("standby")

    def show_page(self, name):
        self.page = name
        for other, frame in self.pages.items():
            if other != name:
                frame.pack_forget()
        self.pages[name].pack(fill="both", expand=True)
        for other, button in self.tab_buttons.items():
            color, ink = (BLUE, "white") if other == name else (FIELD, INK)
            button.config(bg=color, fg=ink, activebackground=color, activeforeground=ink)
        if name == "media":
            self._fill_media_list()
        elif name == "order" and self.media:
            self._fill_order()
            self._fill_order_choices()

    def _set_mode(self, mode, what=""):
        """Media live / scripture live / standby / black, and the line that tells the operator which."""
        self.mode = mode
        if mode not in ("media", "video"):
            self.live_media = None
        if mode != "hymn":
            self._end_hymn()
        if not self.looping_now:  # anything the operator puts up ends the announcement loop
            self._stop_loop()
        if mode != "video" and self.projector.video_shown:  # the verse/poster is up: the video waits where it was
            self.projector.hide_video()
            if self.player:
                self.player.pause()
        text, color = {
            "scripture": (f"● LIVE: {what}", GREEN),
            "media": (f"● LIVE: {getattr(what, 'title', what)} ({MEDIA_KINDS.get(getattr(what, 'kind', ''), 'Media')})",
                      GOLD),
            "video": (f"● LIVE: {getattr(what, 'title', what)} ({MEDIA_KINDS.get(getattr(what, 'kind', ''), 'Video')})",
                      GOLD),
            "hymn": (f"● LIVE: {what}", GOLD),
            "black": ("● LIVE: BLACK SCREEN", RED),
        }.get(mode, ("Screen: standby", MUTED))
        self.live_label.config(text=text, fg=color)
        self.live_note.config(text="")
        self._update_next()

    def black_screen(self):
        if self.live_ref:
            self._remember(self._live_state())
        self.live_ref = None
        self.projector.black()
        self._set_mode("black")

    def _use_standby_image(self, path):
        """The operator's chosen standby picture, if it is still there; otherwise the logo in data/standby.jpg."""
        if not path:
            return
        try:
            self.projector.set_standby(load_picture(path))
        except Exception:
            logging.warning("Standby picture %s could not be opened; keeping the logo", path)

    # ---- media page ----
    def _build_media_page(self, page):
        self.media_kind, self.media_items, self.media_selected, self.media_picture = "announcement", [], None, None
        self.seeking = False
        if not self.media:
            self._label(page, self.media_error or "Media library is off.", 13, RED, "bold").pack(padx=20, pady=20)
            return
        main = tk.Frame(page, bg=BG, padx=12, pady=6)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=2, uniform="m")
        main.columnconfigure(1, weight=3, uniform="m")
        main.rowconfigure(0, weight=1)

        left = tk.Frame(main, bg=BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        kinds = tk.Frame(left, bg=BG)
        kinds.pack(fill="x")
        self.kind_buttons = {}
        for kind, text in (("announcement", "ANNOUNCEMENTS"), ("hymn", "HYMNS"), ("video", "VIDEOS"),
                           ("image", "IMAGES"), ("audio", "MUSIC")):
            button = tk.Button(kinds, text=text, command=lambda k=kind: self._pick_kind(k), relief="flat", bd=0,
                               padx=6, pady=4, font=self.font(9, "bold"), takefocus=0, cursor="hand2")
            button.pack(side="left", padx=(0, 3))
            self.kind_buttons[kind] = button
        search = tk.Frame(left, bg=BG)
        search.pack(fill="x", pady=(8, 4))
        self._label(search, "Search").pack(side="left")
        self.media_search = tk.Entry(search, bg=FIELD, fg=INK, insertbackground=INK, relief="flat", font=self.font(12))
        self.media_search.pack(side="left", fill="x", expand=True, padx=(8, 0), ipady=4)
        self.media_search.bind("<KeyRelease>", lambda _e: self._fill_media_list())
        self.media_list = tk.Listbox(left, bg=PANEL, fg=INK, selectbackground=BLUE, relief="flat", bd=0,
                                     highlightthickness=0, font=self.font(12), activestyle="none", takefocus=0,
                                     exportselection=False)
        self.media_list.pack(fill="both", expand=True, pady=(2, 6))
        self.media_list.bind("<<ListboxSelect>>", self._pick_media)
        self.tool_rows = {}
        for name, buttons in (("files", (("+ ADD", self.add_media), ("PASTE LINK", self.paste_link),
                                         ("RENAME", self.rename_media), ("DELETE", self.delete_media))),
                              ("hymn", (("FIND HYMN", self.find_hymn), ("+ NEW HYMN", self.new_hymn), ("IMPORT", self.import_hymns),
                                        ("EDIT", self.edit_hymn), ("DELETE", self.delete_hymn)))):
            tools = self.tool_rows[name] = tk.Frame(left, bg=BG)
            for text, command in buttons:
                strong = text in ("PASTE LINK", "FIND HYMN")
                tk.Button(tools, text=text, command=command, bg=BLUE if strong else FIELD, fg="white" if strong else
                          INK, relief="flat", bd=0, padx=10, pady=6, font=self.font(10, "bold"), takefocus=0,
                          cursor="hand2").pack(side="left", padx=(0, 6))
        self.tool_rows["files"].pack(fill="x")

        card = tk.Frame(main, bg=PANEL, padx=18, pady=14)
        card.grid(row=0, column=1, sticky="nsew")
        card.pack_propagate(False)  # same freeze guard as the verse preview
        self.media_card = card
        self.media_alert = self._label(card, "PREVIEW", 9, BLUE, "bold")
        self.media_alert.pack(anchor="w")
        self.media_title = self._label(card, "Nothing selected", 18, GOLD, "bold", anchor="w")
        self.media_title.pack(fill="x", pady=(4, 2))
        self.media_info = self._label(card, "", 10, MUTED, anchor="w", justify="left")
        self.media_info.pack(fill="x")
        card.bind("<Configure>", lambda e: self.media_info.config(wraplength=e.width - 40))
        self.media_thumb = tk.Label(card, bg="black", fg=MUTED, font=self.font(14, "bold"))
        self.media_thumb.pack(fill="both", expand=True, pady=8)
        self.media_fields = tk.Frame(card, bg=PANEL)
        self._label(self.media_fields, "Date").grid(row=0, column=0, sticky="w")
        self.media_date = tk.Entry(self.media_fields, bg=FIELD, fg=INK, insertbackground=INK, relief="flat",
                                   font=self.font(11), width=16)
        self.media_date.grid(row=0, column=1, sticky="w", padx=6, ipady=3)
        self._label(self.media_fields, "Description").grid(row=0, column=2, sticky="w", padx=(8, 0))
        self.media_text = tk.Entry(self.media_fields, bg=FIELD, fg=INK, insertbackground=INK, relief="flat",
                                   font=self.font(11))
        self.media_text.grid(row=0, column=3, sticky="ew", padx=6, ipady=3)
        self.media_fields.columnconfigure(3, weight=1)
        tk.Button(self.media_fields, text="SAVE", command=self.save_media_details, bg=FIELD, fg=INK, relief="flat",
                  bd=0, padx=10, pady=3, takefocus=0, cursor="hand2").grid(row=0, column=4, rowspan=2, sticky="ns")
        self._label(self.media_fields, "Show from").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.media_from = tk.Entry(self.media_fields, bg=FIELD, fg=INK, insertbackground=INK, relief="flat",
                                   font=self.font(11), width=16)
        self.media_from.grid(row=1, column=1, sticky="w", padx=6, ipady=3, pady=(4, 0))
        self._label(self.media_fields, "until").grid(row=1, column=2, sticky="w", padx=(8, 0), pady=(4, 0))
        self.media_until = tk.Entry(self.media_fields, bg=FIELD, fg=INK, insertbackground=INK, relief="flat",
                                    font=self.font(11), width=16)
        self.media_until.grid(row=1, column=3, sticky="w", padx=6, ipady=3, pady=(4, 0))
        self._build_hymn_preview(card)

        self._build_video_controls(page)
        self._build_hymn_controls(page)
        self._build_music_controls(page)
        bottom = self.media_bottom = tk.Frame(page, bg=PANEL, padx=12, pady=10)
        bottom.pack(fill="x")
        self.standby_button = self._button(bottom, "USE AS STANDBY PICTURE", FIELD, self.use_as_standby, 22)
        self.standby_button.pack(side="left")
        self.ready_button = self._button(bottom, "GET READY (buffer)", BLUE, self.get_ready, 18)
        self.loop_button = self._button(bottom, "LOOP TODAY'S POSTERS", BLUE, self.toggle_loop, 20)
        self.hymn_bg_button = self._button(bottom, "USE AS HYMN BACKGROUND", FIELD, self.use_as_hymn_background, 23)
        self.media_status = self._label(bottom, "", 10, wraplength=360, justify="left")
        self.media_status.pack(side="left", padx=16)
        self._button(bottom, "PUSH TO LIVE (Space)", GREEN, self.push_media, 20).pack(side="right")
        self.sound_out = self._combo(bottom, 22, lambda _e: self._sound_out_picked())
        self.sound_out.configure(postcommand=self._fill_sound_outputs)
        self.sound_out.set(self.settings.get("audio_device_label") or "Laptop default")
        self.sound_out.pack(side="right", padx=(0, 12))
        self._label(bottom, "Sound out").pack(side="right", padx=(0, 4))
        self._pick_kind("announcement")

    def _build_video_controls(self, page):
        """Play, pause, skip, seek and volume for the video in the player, whichever item is selected."""
        holder = self.video_bar = tk.Frame(page, bg=FIELD, padx=12, pady=6)
        self.video_now = self._label(holder, "No video loaded", 10, MUTED, "bold", anchor="w")
        self.video_now.pack(fill="x")
        bar = tk.Frame(holder, bg=FIELD)
        bar.pack(fill="x", pady=(4, 0))

        def control(text, command, width=5, color=PANEL):
            button = tk.Button(bar, text=text, command=command, bg=color, fg="white", activebackground=color,
                               activeforeground="white", relief="flat", bd=0, width=width, pady=5,
                               font=self.font(11, "bold"), takefocus=0, cursor="hand2")
            button.pack(side="left", padx=2)
            return button

        control("⏮", lambda: self._video_do("restart"), 3)
        control(f"−{SKIP_SECONDS}s", lambda: self._video_do("skip", -SKIP_SECONDS))
        self.play_button = control("▶ PLAY", lambda: self._video_do("toggle"), 9, GREEN)
        control(f"+{SKIP_SECONDS}s", lambda: self._video_do("skip", SKIP_SECONDS))
        control("■ STOP", self.stop_video, 7, RED)
        self.video_time = self._label(bar, "0:00 / 0:00", 10, INK, "bold", width=15)
        self.video_time.pack(side="left", padx=(8, 4))
        self.seek = ttk.Scale(bar, from_=0, to=1, orient="horizontal", takefocus=0)
        self.seek.pack(side="left", fill="x", expand=True, padx=4)
        self.seek.bind("<ButtonPress-1>", lambda _e: setattr(self, "seeking", True))
        self.seek.bind("<ButtonRelease-1>", self._seek_released)
        self.mute = tk.BooleanVar(value=False)
        tk.Checkbutton(bar, text="Mute", variable=self.mute, bg=FIELD, fg=INK, selectcolor=PANEL, activebackground=FIELD,
                       activeforeground=INK, takefocus=0, command=lambda: self._video_do("set_mute", self.mute.get())
                       ).pack(side="left", padx=(8, 2))
        self._label(bar, "Vol").pack(side="left")
        self.volume = ttk.Scale(bar, from_=0, to=100, orient="horizontal", length=90, takefocus=0,
                                command=lambda v: self._video_do("set_volume", float(v)))
        self.volume.set(int(self.settings.get("video_volume", 100)) if str(self.settings.get("video_volume", "")
                                                                          ).isdigit() else 100)
        self.volume.pack(side="left")

    def _pick_kind(self, kind):
        self.media_kind = kind
        for name, row in self.tool_rows.items():
            row.pack_forget()
        self.tool_rows["hymn" if kind == "hymn" else "files"].pack(fill="x")
        for other, button in self.kind_buttons.items():
            color, ink = (BLUE, "white") if other == kind else (FIELD, INK)
            button.config(bg=color, fg=ink, activebackground=color, activeforeground=ink)
        self._fill_media_list()
        self._show_media(None)

    def _fill_media_list(self):
        if not self.media:
            return
        if self.media_kind == "hymn":
            self._fill_hymn_list()
            return
        chosen = self.media_selected.id if self.media_selected else None
        self.media_items = self.media.items(self.media_kind, self.media_search.get())
        self.media_list.delete(0, "end")
        for row, item in enumerate(self.media_items):
            live = self.live_media and self.live_media.id == item.id
            mark = "● " if live else "▶ " if item.is_video and self.media_kind != "video" else ""
            if self.music_item and self.music_item.id == item.id and self.music and self.music.loaded is not None:
                mark = "♪ "
            note = f"   ({item.schedule_note})" if item.schedule_note else ""
            self.media_list.insert("end", mark + item.label + ("   (link)" if item.url else "") + note)
            if note:
                self.media_list.itemconfig("end", fg=MUTED)
            if item.id == chosen:
                self.media_list.selection_set(row)

    def _pick_media(self, _event):
        selected = self.media_list.curselection()
        if selected and self.media_kind == "hymn":
            self._show_hymn(self.media_items[selected[0]])
        elif selected:
            self._show_media(self.media_items[selected[0]])

    def _show_media(self, item):
        """Preview only: nothing reaches the projector until PUSH TO LIVE."""
        self.media_selected, self.media_picture = item, None
        self.media_thumb.config(image="", text="")
        self.media_thumb.image = None
        self.media_fields.pack_forget()
        self.hymn_preview.pack_forget()
        self.media_thumb.pack(fill="both", expand=True, pady=8)
        video = bool(item and item.is_video)
        for button in (self.ready_button, self.standby_button, self.loop_button, self.hymn_bg_button):
            button.pack_forget()
        if video:
            self.ready_button.pack(side="left", before=self.media_status)
        elif self.media_kind == "announcement":
            self.loop_button.pack(side="left", before=self.media_status)
        elif self.media_kind == "image":
            self.standby_button.pack(side="left", before=self.media_status)
            self.hymn_bg_button.pack(side="left", padx=(8, 0), before=self.media_status)
        if not item:
            self.media_title.config(text="Nothing selected")
            self.media_info.config(text={"video": "Pick a video, + ADD a video file, or PASTE LINK for one on "
                                                  "YouTube, Facebook or Vimeo (played as a stream, not downloaded).",
                                         "audio": "Music plays through the sound output on its own, so it can run "
                                                  "under a poster. + ADD brings in MP3, WAV and other recordings.",
                                         "announcement": "Pick a poster, or + ADD. Give it Show from / until dates "
                                                         "and LOOP TODAY'S POSTERS cycles the current ones."}
                                   .get(self.media_kind, "Pick an item on the left, or + ADD to bring in pictures."))
            self.media_alert.config(text="PREVIEW", fg=BLUE)
            return
        self.media_title.config(text=item.title)
        detail = item.url if item.url else f"added {item.added}"
        self.media_info.config(text=f"{MEDIA_KINDS[item.kind]}{' video' if video and item.kind != 'video' else ''}"
                               f" · {detail}" + (f" · {item.description}" if item.description else ""))
        live = self.live_media and self.live_media.id == item.id
        self.media_alert.config(text="PREVIEW · ON SCREEN NOW" if live else "PREVIEW", fg=GREEN if live else BLUE)
        if item.kind == "announcement":
            self.media_fields.pack(fill="x", before=self.media_thumb, pady=(6, 0))
            for entry, value in ((self.media_date, item.event_date), (self.media_text, item.description),
                                 (self.media_from, item.show_from), (self.media_until, item.show_until)):
                entry.delete(0, "end")
                entry.insert(0, value)
        if item.is_audio:
            self.media_thumb.config(text="♪  MUSIC\n\n" + item.path.name + "\n\nPUSH TO LIVE plays it; the screen stays "
                                    "as it is.")
            if not item.path.exists():
                self.media_alert.config(text="THIS FILE IS MISSING: DELETE IT AND ADD IT AGAIN", fg=RED)
            return
        if video:
            stream = self.streams.get(item.id)
            if item.url and stream and getattr(stream, "picture", None) is not None:
                self._set_thumb(stream.picture)
            else:
                self.media_thumb.config(text="▶  VIDEO\n\n" + ("Online link: needs internet when played."
                                                              if item.url else item.path.name))
            if not item.url and not item.path.exists():
                self.media_alert.config(text="THIS FILE IS MISSING: DELETE IT AND ADD IT AGAIN", fg=RED)
            return
        try:
            self.media_picture = load_picture(item.path)
        except Exception:
            logging.exception("Could not open %s", item.path)
            self.media_alert.config(text="THIS FILE IS MISSING OR DAMAGED: DELETE IT AND ADD IT AGAIN", fg=RED)
            return
        self._set_thumb(self.media_picture)

    def _set_thumb(self, picture):
        self.root.update_idletasks()
        width = max(200, self.media_card.winfo_width() - 40)
        height = max(120, min(int(width * 9 / 16), self.media_card.winfo_height() - 150))
        fit = min(width / picture.width, height / picture.height)  # small link pictures are enlarged too
        thumb = picture.resize((max(1, round(picture.width * fit)), max(1, round(picture.height * fit))), Image.LANCZOS)
        image = ImageTk.PhotoImage(thumb)
        self.media_thumb.config(image=image, text="")
        self.media_thumb.image = image

    def push_media(self):
        if self.media_kind == "hymn":
            self.push_hymn()
            return
        item = self.media_selected
        if item and item.is_audio:
            self.play_music(item)
            return
        if item and (item.is_video or item.is_animated):
            self.push_video(item)
            return
        picture = self.media_picture
        if not item or picture is None:
            self.media_status.config(text="Pick a picture first." if not item else "That file cannot be shown.")
            return
        self._take_screen()
        self.projector.show_picture(picture)
        self._set_mode("media", item)
        self.live_media = item
        logging.info("Media live: %s (%s)", item.title, item.kind)
        self.media_alert.config(text="PREVIEW · ON SCREEN NOW", fg=GREEN)
        self._fill_media_list()

    def _take_screen(self):
        if self.live_ref:
            self._remember(self._live_state())  # Undo brings the verse back
        self.live_ref = None

    # ---- video ----
    def _player(self):
        """The player lives inside the projector window; if that window was ever rebuilt, so is the player."""
        mirror = self.projector.mirror
        wid = (self.projector.video_window_id(), mirror.video_window_id() if mirror else None)
        if self.player and self.player_window == wid:
            return self.player
        if self.player:
            self.player.close()
            self.player, self.video_item = None, None
        try:
            options = self.settings.get("video_player_options", {})
            self.player = video_module.Player(wid[0], audio_device=self.settings.get("audio_device"), **options)
            if mirror:  # projector + TV: the TV gets its own silent copy (see video.MirroredPlayer)
                try:
                    self.player = video_module.MirroredPlayer(self.player, video_module.Player(wid[1], **options))
                except Exception:
                    logging.exception("Second screen video player could not start; video shows on the first only")
        except Exception as error:
            logging.exception("Video player could not start")
            self.player_error = ("The video player is not installed on this laptop. Run setup again (it needs "
                                 "internet once)." if isinstance(error, OSError) else f"Video player failed: {error}")
            self.media_status.config(text=self.player_error, fg=RED)
            return None
        logging.info("Video player started")
        self.player_window, self.player_error = wid, None
        self.player.set_volume(self.volume.get())
        self.player.set_mute(self.mute.get())
        return self.player

    def _source(self, item):
        """What the player opens: the file, or the stream behind the link (None while it still has to be found)."""
        if not item.url:
            if not item.path.exists():
                self.media_status.config(text="That video file is missing. Delete it and add it again.", fg=RED)
                return None
            return item.path
        stream = self.streams.get(item.id)
        return stream if stream and not stream.stale() else None

    def get_ready(self, then_play=False):
        """Load the video paused, out of sight, so it fills its buffer before anyone presses play."""
        item = self.media_selected
        if not item or not (item.is_video or item.is_animated):
            return
        source = self._source(item)
        if source is None:
            if item.url:
                self._find_link(item, then_play=then_play)
            return
        player = self._player()
        if not player:
            return
        if not (self.video_item and self.video_item.id == item.id and player.loaded is not None):
            player.load(source, loop=item.is_animated)  # an animated GIF goes round and round
            self.video_item = item
        self.retried.discard(item.id)
        if then_play:
            self._go_live_video(item)
        else:
            self.media_status.config(text=f"\"{item.title}\" is loading out of sight. PUSH TO LIVE when it is time.",
                                     fg=MUTED)

    def push_video(self, item):
        self.get_ready(then_play=True)

    def _go_live_video(self, item):
        if self.music and not self.music.paused:  # one sound at a time: the video brings its own
            self.music.pause()
            self.media_status.config(text="Music paused while the video plays.", fg=MUTED)
        self._take_screen()
        self.projector.show_video()
        self._set_mode("video", item)
        self.live_media = item
        self.player.play()
        logging.info("Video live: %s (%s)", item.title, item.url or item.path.name)
        self.media_status.config(text="", fg=MUTED)
        self._fill_media_list()

    def _find_link(self, item, then_play=False, url=None):
        """Ask the website for the stream behind a link, on a worker thread (it can take 5-30 seconds)."""
        key = item.id if item else url
        if key in self.finding:
            return
        self.finding.add(key)
        self.media_status.config(text="Getting the video from the link... (up to 30 seconds)", fg=GOLD)

        def work():
            try:
                stream = _app().find_stream(item.url if item else url)
                stream.picture = None
                self.events.put(("stream_found", (key, item, stream, then_play)))
            except VideoError as error:
                self.events.put(("stream_failed", (key, str(error))))
                return
            except Exception as error:
                logging.exception("Link lookup failed")
                self.events.put(("stream_failed", (key, f"The link could not be opened: {error}")))
                return
            try:  # only the preview picture: the video is already getting ready, so this may take its time
                stream.picture = next((p for p in map(self._fetch_thumb, stream.thumbnails) if p), None)
                if stream.picture is not None:
                    self.events.put(("stream_thumb", stream))
            except Exception:
                logging.exception("Preview picture failed")

        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _fetch_thumb(address):
        if not address:
            return None
        try:
            from urllib.request import Request, urlopen

            with urlopen(Request(address, headers={"User-Agent": "Mozilla/5.0"}), timeout=10) as answer:
                return load_picture(io.BytesIO(answer.read(5_000_000)))
        except Exception:
            return None

    def _media_event(self, name, value):
        if name == "stream_failed":
            key, message = value
            self.finding.discard(key)
            self.media_status.config(text=message, fg=RED)
            messagebox.showwarning("Video link", message, parent=self.root)
        elif name == "stream_found":
            key, item, stream, then_play = value
            self.finding.discard(key)
            if item is None:  # a newly pasted link: keep it in the library for next time
                item = self.media.add_link(stream.url, stream.title, kind="video" if self.media_kind != "announcement"
                                           else "announcement")
                self.media_search.delete(0, "end")
            self.streams[item.id] = stream
            self.media_status.config(text=f"Found: {stream.title}" + (" (LIVE broadcast)" if stream.is_live else
                                     f" ({fmt_time(stream.duration)})" if stream.duration else ""), fg=GREEN)
            if self.media_selected is None or self.media_selected.id == item.id or key != item.id:
                self.media_selected = item
                self._fill_media_list()
                self._show_media(item)
                self.get_ready(then_play=then_play)
        elif name == "stream_thumb":
            item = self.media_selected
            if item and self.streams.get(item.id) is value:
                self._set_thumb(value.picture)
        elif name == "video_copied":
            kind, stored, original = value
            item = self.media.register(kind, stored, original)
            self.media_status.config(text=f"Added {item.title}. It stays here for next Sunday.", fg=GREEN)
            if self.media_kind == kind:
                self.media_selected = item
                self._fill_media_list()
                self._show_media(item)
        elif name == "copy_failed":
            self.media_status.config(text=value, fg=RED)

    def _video_do(self, action, *args):
        if not self.player or self.player.loaded is None:
            selected = self.media_selected
            if action == "toggle" and selected and self.media_kind != "hymn" and (selected.is_video or
                                                                                 selected.is_animated):
                self.push_video(selected)  # PLAY on a picked video: just play it (owner, 2026-11-17)
                return
            if action != "set_volume" and action != "set_mute":
                self.media_status.config(text="Pick a video on the left first, then press PLAY.", fg=GOLD)
            if action == "set_volume":
                self.settings["video_volume"] = int(float(args[0]))
            return
        if action == "toggle" and self.player.paused and self.mode != "video" and self.video_item:
            self._go_live_video(self.video_item)  # PLAY while it is out of sight: it comes on screen first
            return
        getattr(self.player, action)(*args)
        if action == "set_volume":
            self.settings["video_volume"] = int(float(args[0]))

    def _seek_released(self, _event):
        self.seeking = False
        self._video_do("seek_to", self.seek.get())

    def stop_video(self):
        if self.player:
            self.player.stop()
        was_live = self.mode == "video"
        self.video_item = None
        if was_live:
            self.clear_live()
        self._fill_media_list()

    def _watch_video(self):
        """Keep the controls truthful, and say what is happening (buffering, ended, connection dropped)."""
        try:
            self._update_video_controls()
        except Exception:
            logging.exception("Video controls update failed")
        if isinstance(self.player, video_module.MirroredPlayer):
            self.player.sync()
        try:
            self._update_service_bars()
        except Exception:
            logging.exception("Hymn/music controls update failed")
        self.root.after(VIDEO_CHECK_MS, self._watch_video)

    def _update_video_controls(self):
        if not self.media:
            return
        player = self.player
        loaded = bool(player and player.loaded is not None and self.video_item)
        video_page = self.media_kind == "video" or bool(self.media_selected and self.media_kind != "hymn" and (
            self.media_selected.is_video or self.media_selected.is_animated)) or (loaded and self.media_kind != "audio")
        if video_page and not self.video_bar.winfo_ismapped():
            self.video_bar.pack(fill="x", before=self.media_bottom)
        elif not video_page and self.video_bar.winfo_ismapped():
            self.video_bar.pack_forget()
        if not loaded:
            picked = self.media_selected if self.media_kind != "hymn" else None
            if picked and (picked.is_video or picked.is_animated):
                self.video_now.config(text=f"PICKED: {picked.title}. Press ▶ PLAY (or PUSH TO LIVE) to show it.",
                                      fg=GOLD)
            else:
                self.video_now.config(text="Pick a video on the left, then press ▶ PLAY", fg=MUTED)
            self.video_time.config(text="0:00 / 0:00")
            self.play_button.config(text="▶ PLAY")
            return
        position, duration = player.position, player.duration
        where = "ON SCREEN" if self.mode == "video" else "READY, NOT ON SCREEN"
        state = "BUFFERING" if player.buffering else "ENDED" if player.ended else "PAUSED" if player.paused else "PLAYING"
        self.video_now.config(text=f"{where} · {state}: {self.video_item.title}"[:110],
                              fg=GREEN if self.mode == "video" and state == "PLAYING" else GOLD)
        ahead = player.buffered_ahead
        self.video_time.config(text=f"{fmt_time(position)} / {fmt_time(duration) if duration else 'LIVE'}")
        self.play_button.config(text="▶ PLAY" if player.paused else "❚❚ PAUSE")
        if duration and not self.seeking:
            self.seek.config(to=duration)
            self.seek.set(position)
        if self.mode == "video":
            self.live_label.config(text=f"● LIVE: {self.video_item.title} (Video) {fmt_time(position)}"
                                        + (f" / {fmt_time(duration)}" if duration else ""))
        if self.video_item.url and ahead and not player.error:
            note = f"{fmt_time(ahead)} loaded ahead" + (" (weak connection: let it load before playing)"
                                                         if ahead < 20 and player.paused and not player.ended else "")
            if self.media_status.cget("fg") != RED:
                self.media_status.config(text=note, fg=MUTED)
        if player.error:
            item = self.video_item
            if item.url and item.id not in self.retried and position < 1:
                # A stream address that failed before it started: look the link up again, once, by itself.
                self.retried.add(item.id)
                player.error = None
                self.streams.pop(item.id, None)
                player.loaded = None
                self.media_selected = item
                self._find_link(item, then_play=self.mode == "video")
            else:
                if self.media_status.cget("text") != player.error:
                    logging.error("Video: %s (%s)", player.error, item.title)
                self.media_status.config(text=player.error, fg=RED)

    def add_media(self):
        kind = self.media_kind
        patterns = " ".join(f"*{ext} *{ext.upper()}" for ext in ACCEPTS[kind])
        name = {"image": "Pictures", "video": "Videos"}.get(kind, "Pictures and videos")
        paths = filedialog.askopenfilenames(parent=self.root, title=f"Add {name.lower()}",
                                            filetypes=[(name, patterns), ("All files", "*")])
        if not paths:
            return
        self.media_status.config(text=f"Adding {len(paths)}... big videos take a moment to copy.", fg=GOLD)

        def work():  # copying a 1 GB video must not freeze the screen during a service
            for path in paths:
                try:
                    stored, original = self.media.store(path, kind)
                    self.events.put(("video_copied", (kind, stored, original)))
                except Exception as error:
                    logging.warning("Could not add %s: %s", path, error)
                    self.events.put(("copy_failed", str(error)))

        threading.Thread(target=work, daemon=True).start()

    def paste_link(self):
        try:
            clipboard = self.root.clipboard_get().strip()
        except tk.TclError:
            clipboard = ""
        url = simpledialog.askstring(
            "Play a video from a link", "Paste the video link (YouTube, Facebook, Vimeo...).\n"
            "It plays as a stream: nothing is downloaded, but it needs internet.",
            initialvalue=clipboard if clipboard.startswith(("http://", "https://")) else "", parent=self.root)
        if not url or not url.strip():
            return
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        if self.media_kind == "image":
            self._pick_kind("video")
        self._find_link(None, url=url)

    def rename_media(self):
        item = self.media_selected
        if not item:
            return
        title = simpledialog.askstring("Rename", "New name:", initialvalue=item.title, parent=self.root)
        if title and title.strip():
            self.media.rename(item.id, title)
            self._refresh_selected(item.id)

    def save_media_details(self):
        item = self.media_selected
        if item and item.kind == "announcement":
            try:
                self.media.schedule(item.id, self.media_from.get(), self.media_until.get())
            except ValueError as error:
                self.media_status.config(text=str(error), fg=RED)
                return
            self.media.describe(item.id, self.media_date.get(), self.media_text.get())
            self._refresh_selected(item.id)
            self.media_status.config(text="Saved.", fg=MUTED)
            self.root.focus_set()

    def _refresh_selected(self, item_id):
        item = self.media.get(item_id)
        if self.live_media and self.live_media.id == item_id:
            self.live_media = item
            self._set_mode(self.mode, item)
        if self.video_item and self.video_item.id == item_id:
            self.video_item = item
        self.media_selected = item
        self._fill_media_list()
        self._show_media(item)

    def delete_media(self):
        item = self.media_selected
        if not item or not messagebox.askyesno("Delete", f"Delete \"{item.title}\" from the library?", parent=self.root):
            return
        if self.live_media and self.live_media.id == item.id:
            self.clear_live()
        if self.video_item and self.video_item.id == item.id:
            self.stop_video()
        if self.music_item and self.music_item.id == item.id:
            self.stop_music()
        if item.path.name and str(item.path) == self.settings.get("hymn_background"):
            self.settings.pop("hymn_background")
            self._save_settings()
        if item.path.name and str(item.path) == self.settings.get("standby_image"):
            self.settings.pop("standby_image")
            self._save_settings()
            self.projector.set_standby(Image.open(STANDBY_IMAGE).convert("RGB") if STANDBY_IMAGE.exists() else None)
        self.streams.pop(item.id, None)
        self.media.delete(item.id)
        self.media_selected = None
        self._fill_media_list()
        self._show_media(None)

    def use_as_standby(self):
        item, picture = self.media_selected, self.media_picture
        if not item or picture is None:
            return
        self.projector.set_standby(picture)
        self.settings["standby_image"] = str(item.path)
        self._save_settings()
        self.media_status.config(text=f"\"{item.title}\" now shows whenever nothing else is up.", fg=MUTED)

