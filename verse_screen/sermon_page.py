"""The SERMON TEST window on the Bible page (owner, 2026-11-18). See sermon_test.py."""
import json
import logging
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, simpledialog
from tkinter import ttk

from .bible import ROOT
from .sermon_test import SermonAudio, SermonFeeder
from .style import BG, BLUE, FIELD, GOLD, GREEN, INK, MUTED, PANEL, RED
from . import video as video_module
from .video import VideoError, fmt_time

TEST_SERMONS = ROOT / "bench" / "sermons"
SERMON_TYPES = "*.opus *.mp3 *.m4a *.wav *.mp4 *.mkv *.webm *.mov *.aac *.ogg *.MP3 *.MP4 *.WAV *.M4A"


def test_sermons(folder=TEST_SERMONS):
    """[(label, path, youtube_id)] for the sermons in bench/sermons, with the pastor and title from yt-dlp."""
    found = []
    for audio in sorted(folder.glob("*/*.opus")) if folder.is_dir() else []:
        label = audio.stem
        info = audio.with_suffix(".info.json")
        try:
            data = json.loads(info.read_text(encoding="utf-8"))
            label = f"{audio.parent.name.split('_', 1)[-1].replace('_', ' ').title()}: {data.get('title', '')}"[:90]
        except (OSError, ValueError):
            pass
        found.append((label, audio, audio.stem))
    return found


class SermonTestControl:
    def _init_sermon_test(self):
        self.sermon_window = self.sermon_player = self.sermon_audio = self.sermon_feeder = None
        self.sermon_name = ""
        self.sermon_seeking = False

    def _sermon_mark(self):
        """'[sermon 12:41] ' in front of log lines during a test, so they line up with the video's transcript."""
        feeder = getattr(self, "sermon_feeder", None)
        return f"[sermon {fmt_time(feeder.position)}] " if feeder and feeder.running else ""

    def show_sermon_test(self):
        if self.sermon_window and self.sermon_window.winfo_exists():
            self.sermon_window.lift()
            return
        window = self.sermon_window = tk.Toplevel(self.root, bg=BG, padx=18, pady=14)
        window.title("Sermon test")
        window.transient(self.root)
        window.protocol("WM_DELETE_WINDOW", self._close_sermon_window)
        self._label(window, "SERMON TEST", 14, GOLD, "bold").pack(anchor="w")
        self._label(window, "Plays a recorded sermon into the speech engine as if it came from the mixer (the "
                            "microphone is off meanwhile). Verses go up as in a service, and every sentence is "
                            "logged with the sermon time, to compare with the video's transcript.", 10, MUTED,
                    wraplength=560, justify="left").pack(anchor="w", pady=(2, 10))
        pick = tk.Frame(window, bg=BG)
        pick.pack(fill="x")
        for text, command in (("PICK A FILE", self._sermon_pick_file), ("PASTE LINK", self._sermon_paste_link)):
            tk.Button(pick, text=text, command=command, bg=BLUE, fg="white", relief="flat", bd=0, padx=12, pady=6,
                      font=self.font(10, "bold"), takefocus=0, cursor="hand2").pack(side="left", padx=(0, 6))
        self.sermon_choices = test_sermons()
        if self.sermon_choices:
            self._label(pick, "or a test sermon").pack(side="left", padx=(8, 4))
            box = self.sermon_box = self._combo(pick, 34, lambda _e: self._sermon_pick_test())
            box["values"] = [label for label, _p, _i in self.sermon_choices]
            box.pack(side="left")
        self.sermon_title = self._label(window, "No sermon loaded", 12, INK, "bold", wraplength=560, justify="left")
        self.sermon_title.pack(anchor="w", pady=(12, 2))
        self.sermon_status = self._label(window, "", 10, MUTED, wraplength=560, justify="left")
        self.sermon_status.pack(anchor="w")
        bar = tk.Frame(window, bg=FIELD, padx=8, pady=8)
        bar.pack(fill="x", pady=(10, 0))

        def control(text, command, width=6, color=PANEL):
            tk.Button(bar, text=text, command=command, bg=color, fg="white", relief="flat", bd=0, width=width, pady=4,
                      font=self.font(10, "bold"), takefocus=0, cursor="hand2").pack(side="left", padx=2)

        control("−1 min", lambda: self._sermon_skip(-60))
        control("−10s", lambda: self._sermon_skip(-10))
        self.sermon_play = tk.Button(bar, text="▶ PLAY", command=self._sermon_toggle, bg=GREEN, fg="white",
                                     relief="flat", bd=0, width=9, pady=4, font=self.font(10, "bold"), takefocus=0)
        self.sermon_play.pack(side="left", padx=2)
        control("+10s", lambda: self._sermon_skip(10))
        control("+1 min", lambda: self._sermon_skip(60))
        self.sermon_time = self._label(bar, "0:00 / 0:00", 10, INK, "bold", width=15)
        self.sermon_time.pack(side="left", padx=6)
        self.sermon_seek = ttk.Scale(bar, from_=0, to=1, orient="horizontal", takefocus=0, length=160)
        self.sermon_seek.pack(side="left", fill="x", expand=True)
        self.sermon_seek.bind("<ButtonPress-1>", lambda _e: setattr(self, "sermon_seeking", True))
        self.sermon_seek.bind("<ButtonRelease-1>", self._sermon_seek_released)
        tk.Button(window, text="STOP TEST (back to the microphone)", command=self.stop_sermon_test, bg=RED,
                  fg="white", relief="flat", bd=0, padx=12, pady=6, font=self.font(10, "bold"), takefocus=0,
                  cursor="hand2").pack(anchor="w", pady=(10, 0))
        self._watch_sermon()

    def _sermon_pick_file(self):
        path = filedialog.askopenfilename(parent=self.sermon_window, title="Pick a sermon recording",
                                          filetypes=[("Sermons", SERMON_TYPES), ("All files", "*")])
        if path:
            self.start_sermon_test(Path(path), Path(path).stem, source_id=str(path))

    def _sermon_pick_test(self):
        index = self.sermon_box.current()
        if index >= 0:
            label, path, video_id = self.sermon_choices[index]
            self.start_sermon_test(path, label, source_id=video_id)

    def _sermon_paste_link(self):
        url = simpledialog.askstring("Sermon link", "Paste the sermon's link (YouTube, Facebook...):",
                                     parent=self.sermon_window)
        if not url or not url.strip():
            return
        self.sermon_status.config(text="Getting the sermon from the link... (up to 30 seconds)", fg=GOLD)

        def work():
            try:
                stream = _app().find_stream(url.strip())
                self.events.put(("sermon_stream", stream))
            except VideoError as error:
                self.events.put(("sermon_failed", str(error)))
            except Exception as error:
                self.events.put(("sermon_failed", f"The link could not be opened: {error}"))

        threading.Thread(target=work, daemon=True).start()

    def _sermon_event(self, name, value):
        if name == "sermon_failed":
            self.sermon_status.config(text=value, fg=RED)
        elif name == "sermon_stream":
            stream = value
            self.start_sermon_test(stream, stream.title, source_id=stream.url,
                                   audio_source=(stream.audio or stream.video, stream.headers))

    def start_sermon_test(self, source, name, source_id="", audio_source=None):
        self.stop_sermon_test(quiet=True)
        options = {k: v for k, v in self.settings.get("video_player_options", {}).items() if k != "video_output"}
        try:
            self.sermon_player = video_module.Player(None, audio_device=self.settings.get("audio_device"), **options)
        except Exception as error:
            logging.exception("Sermon test player could not start")
            self.sermon_status.config(text=f"The player could not start: {error}", fg=RED)
            return
        address, headers = audio_source if audio_source else (source, None)
        self.sermon_audio = SermonAudio(address, headers)
        if self.source:  # the microphone rests; the sermon file speaks instead
            self.source.stop()
        self.source = self.sermon_feeder = SermonFeeder(self.ring, self.sermon_audio, self.sermon_player)
        self.sermon_player.load(source, play=True)
        self.sermon_name = name
        logging.info("Sermon test started: %s | %s", name, source_id)
        self.sermon_title.config(text=name)
        self.transcript.config(text="Sermon test: listening to the recording...")

    def stop_sermon_test(self, quiet=False):
        if not self.sermon_feeder:
            return
        logging.info("Sermon test stopped at %s: %s", fmt_time(self.sermon_feeder.position), self.sermon_name)
        self.sermon_feeder.stop()
        self.sermon_audio.stop()
        self.sermon_player.close()
        if self.source is self.sermon_feeder:
            self.source = None
        self.sermon_feeder = self.sermon_audio = self.sermon_player = None
        if not quiet:
            self._start_audio(remember=False)  # back to the laptop's sound input
            if self.sermon_window and self.sermon_window.winfo_exists():
                self.sermon_title.config(text="Test stopped. The log has everything for the comparison.")
                self.sermon_status.config(text="")

    def _close_sermon_window(self):
        self.stop_sermon_test()
        self.sermon_window.destroy()
        self.sermon_window = None

    def _sermon_toggle(self):
        if self.sermon_player:
            self.sermon_player.toggle()

    def _sermon_skip(self, seconds):
        if self.sermon_player:
            self.sermon_player.skip(seconds)

    def _sermon_seek_released(self, _event):
        self.sermon_seeking = False
        if self.sermon_player:
            self.sermon_player.seek_to(self.sermon_seek.get())

    def _watch_sermon(self):
        window = self.sermon_window
        if not window or not window.winfo_exists():
            return
        try:
            player, audio = self.sermon_player, self.sermon_audio
            if player and audio:
                position, duration = player.position, player.duration
                self.sermon_time.config(text=f"{fmt_time(position)} / {fmt_time(duration)}")
                self.sermon_play.config(text="▶ PLAY" if player.paused else "❚❚ PAUSE")
                if duration and not self.sermon_seeking:
                    self.sermon_seek.config(to=duration)
                    self.sermon_seek.set(position)
                if audio.error:
                    self.sermon_status.config(text=f"The sermon's sound could not be read: {audio.error}", fg=RED)
                elif not audio.done:
                    self.sermon_status.config(text=f"Reading the sermon: {fmt_time(audio.seconds)} ready "
                                                   "(it keeps ahead of the playing).", fg=MUTED)
                else:
                    self.sermon_status.config(text="Listening. Every sentence is in the log with its sermon time.",
                                              fg=GREEN)
        except Exception:
            logging.exception("Sermon test window update failed")
        window.after(500, self._watch_sermon)


def _app():
    from . import app
    return app
