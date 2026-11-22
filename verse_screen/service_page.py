"""Hymns, music, the announcement loop, the sound-output choice and the service order (phases 5-7, 2026-11-17).

Mixed into App through MediaControl: it uses the App's projector, media library, settings and screen modes.
"""
import logging
import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter import ttk

from .hymn_find_page import HymnFinder
from .hymn_search import read_docx, read_pdf, split_hymn_book
from .hymns import lyrics_text, parse_lyrics, parse_openlyrics, parse_text_file, sequence
from .media import CURRENT_ORDER, load_picture
from .style import BG, BLUE, FIELD, GOLD, GREEN, INK, MUTED, PANEL, RED
from . import video as video_module
from .video import SKIP_SECONDS, fmt_time

TEXT_SIZES = {"Large": 1.0, "Medium": 0.8, "Small": 0.65}
LOOP_SECONDS = 8
ORDER_TAGS = {"announcement": "Poster", "image": "Image", "video": "Video", "audio": "Music"}


class ServiceControl(HymnFinder):
    def _init_service(self):
        self.hymn_selected = self.hymn_live = None
        self.hymn_seq, self.hymn_pos, self.hymn_start, self.hymn_job = [], 0, 0, None
        self.hymn_backdrop = None
        self.music = self.music_item = None
        self.music_seeking = False
        self.loop_job, self.looping_now, self.loop_items, self.loop_pos = None, False, [], 0
        self.sound_devices = {}
        self.order, self.order_live = [], None
        self.order_kind, self.order_choices = "announcement", []
        self._load_hymn_background(self.settings.get("hymn_background"))
        if self.media:
            try:
                self.order = self.media.playlist(CURRENT_ORDER)
            except Exception:
                logging.exception("Could not read the saved service order")

    # ---- hymns ----
    def _build_hymn_preview(self, card):
        self.hymn_preview = tk.Listbox(card, bg="black", fg=INK, selectbackground=BLUE, relief="flat", bd=0,
                                       highlightthickness=0, font=self.font(12), activestyle="none", takefocus=0,
                                       exportselection=False)
        self.hymn_preview.bind("<<ListboxSelect>>", self._pick_stanza)

    def _build_hymn_controls(self, page):
        bar = self.hymn_bar = tk.Frame(page, bg=FIELD, padx=12, pady=8)
        for text, delta in (("◀ STANZA", -1), ("STANZA ▶", 1)):
            tk.Button(bar, text=text, command=lambda d=delta: self.hymn_step(d), bg=PANEL, fg="white", relief="flat",
                      bd=0, padx=10, pady=5, font=self.font(11, "bold"), takefocus=0, cursor="hand2").pack(side="left",
                                                                                                         padx=2)
        self.hymn_now = self._label(bar, "No hymn on screen", 10, MUTED, "bold", width=18, anchor="w")
        self.hymn_now.pack(side="left", padx=(10, 6))
        self.repeat_chorus = tk.BooleanVar(value=self.settings.get("hymn_repeat_chorus", True) is not False)
        tk.Checkbutton(bar, text="Chorus each verse", variable=self.repeat_chorus, bg=FIELD, fg=INK,
                       selectcolor=PANEL, activebackground=FIELD, activeforeground=INK, takefocus=0,
                       command=self._hymn_options_changed).pack(side="left", padx=4)
        self._label(bar, "Text").pack(side="left", padx=(8, 2))
        self.hymn_size = self._combo(bar, 8, lambda _e: self._hymn_options_changed())
        self.hymn_size["values"] = list(TEXT_SIZES)
        saved = self.settings.get("hymn_text_size")
        self.hymn_size.set(saved if saved in TEXT_SIZES else next(iter(TEXT_SIZES)))
        self.hymn_size.pack(side="left")
        self._label(bar, "Auto next").pack(side="left", padx=(10, 2))
        self.hymn_auto = tk.Spinbox(bar, from_=0, to=120, width=4, bg=FIELD, fg=INK, buttonbackground=PANEL,
                                    relief="flat", insertbackground=INK, command=self._hymn_options_changed)
        self.hymn_auto.delete(0, "end")
        auto = self.settings.get("hymn_auto_seconds", 0)
        self.hymn_auto.insert(0, str(int(auto)) if isinstance(auto, (int, float)) and 0 <= auto <= 120 else "0")
        self.hymn_auto.bind("<KeyRelease>", lambda _e: self._hymn_options_changed())
        self.hymn_auto.pack(side="left")
        self._label(bar, "s (0 off)").pack(side="left", padx=(2, 0))

    def _hymn_options_changed(self):
        self.settings["hymn_repeat_chorus"] = bool(self.repeat_chorus.get())
        self.settings["hymn_text_size"] = self.hymn_size.get()
        self.settings["hymn_auto_seconds"] = self._auto_seconds()
        self._save_settings()
        if self.hymn_live:  # the hymn on screen follows at once, staying on the stanza it is on
            current = self.hymn_seq[self.hymn_pos]
            self.hymn_seq = sequence(self.hymn_live, self.repeat_chorus.get())
            self.hymn_pos = next((i for i, s in enumerate(self.hymn_seq) if s is current),
                                 min(self.hymn_pos, len(self.hymn_seq) - 1))
            self._show_stanza()
        elif self.hymn_selected:
            self._show_hymn(self.hymn_selected)

    def _auto_seconds(self):
        try:
            return max(0, min(120, int(self.hymn_auto.get())))
        except (ValueError, tk.TclError):
            return 0

    def _fill_hymn_list(self):
        chosen = self.hymn_selected.id if self.hymn_selected else None
        self.media_items = self.media.hymns(self.media_search.get())
        self.media_list.delete(0, "end")
        for row, hymn in enumerate(self.media_items):
            live = self.hymn_live and self.hymn_live.id == hymn.id
            extra = f"   ({hymn.collection})" if hymn.collection else ""
            self.media_list.insert("end", ("● " if live else "") + hymn.label + extra)
            if hymn.id == chosen:
                self.media_list.selection_set(row)
        if not self.media_items and not self.media_search.get():
            self._show_media(None)
            self.media_info.config(text="No hymns yet. FIND HYMN searches 270 free hymns (and the internet). + NEW HYMN "
                                        "to type or paste one, or IMPORT your hymn book (Word, PDF or text) or OpenLyrics "
                                        "(.xml) files from other worship software.")

    def _show_hymn(self, hymn):
        self._show_media(None)
        self.hymn_selected, self.hymn_start = hymn, 0
        self.media_thumb.pack_forget()
        self.hymn_preview.pack(fill="both", expand=True, pady=8)
        self.media_title.config(text=hymn.label)
        stanzas = sequence(hymn, self.repeat_chorus.get())
        self.media_info.config(text=" · ".join(filter(None, [hymn.collection, f"{len(hymn.stanzas)} stanzas",
                                                             "Click a stanza to start there."])))
        self.hymn_preview.delete(0, "end")
        for stanza in stanzas:
            self.hymn_preview.insert("end", f"{stanza.label}:  {stanza.text.split(chr(10))[0]}")
        live = self.hymn_live and self.hymn_live.id == hymn.id
        self.media_alert.config(text="PREVIEW · ON SCREEN NOW" if live else "PREVIEW", fg=GREEN if live else BLUE)
        if live:
            self.hymn_preview.selection_set(self.hymn_pos)

    def _pick_stanza(self, _event):
        chosen = self.hymn_preview.curselection()
        if not chosen:
            return
        if self.hymn_live and self.hymn_selected and self.hymn_live.id == self.hymn_selected.id:
            self.hymn_pos = min(chosen[0], len(self.hymn_seq) - 1)  # already up: jump straight there
            self._show_stanza()
        else:
            self.hymn_start = chosen[0]

    def push_hymn(self, hymn=None, start=None):
        hymn = hymn or self.hymn_selected
        if not hymn or not hymn.stanzas:
            self.media_status.config(text="Pick a hymn first." if not hymn else "This hymn has no words yet: EDIT it.",
                                     fg=GOLD)
            return
        self._take_screen()
        self.hymn_live = hymn
        self.hymn_seq = sequence(hymn, self.repeat_chorus.get())
        self.hymn_pos = min(self.hymn_start if start is None else start, len(self.hymn_seq) - 1)
        self._show_stanza()
        if self.media_kind == "hymn":
            self._fill_hymn_list()

    def _hymn_scale(self):
        return TEXT_SIZES.get(self.hymn_size.get(), 1.0)

    def _show_stanza(self):
        hymn, stanza = self.hymn_live, self.hymn_seq[self.hymn_pos]
        count = len(self.hymn_seq)
        self.projector.show(f"{hymn.title}  ·  {stanza.label}", stanza.text, scale=self._hymn_scale(),
                            backdrop=self.hymn_backdrop)
        self._set_mode("hymn", f"{hymn.title} · {stanza.label} ({self.hymn_pos + 1}/{count})")
        logging.info("Hymn live: %s, %s", hymn.title, stanza.label)
        self.hymn_now.config(text=f"{stanza.label} ({self.hymn_pos + 1} of {count})", fg=GREEN)
        if self.hymn_selected and self.hymn_selected.id == hymn.id and self.hymn_preview.winfo_ismapped():
            self.hymn_preview.selection_clear(0, "end")
            self.hymn_preview.selection_set(self.hymn_pos)
            self.hymn_preview.see(self.hymn_pos)
            self.media_alert.config(text="PREVIEW · ON SCREEN NOW", fg=GREEN)
        self._schedule_next_stanza()

    def hymn_step(self, delta):
        if not self.hymn_live:
            return
        target = self.hymn_pos + delta
        if not 0 <= target < len(self.hymn_seq):
            self.media_status.config(text="That was the last stanza. TAKE OFF LIVE when the singing ends."
                                     if delta > 0 else "This is the first stanza.", fg=GOLD)
            return
        self.hymn_pos = target
        self._show_stanza()

    def _schedule_next_stanza(self):
        if self.hymn_job:
            self.root.after_cancel(self.hymn_job)
            self.hymn_job = None
        seconds = self._auto_seconds()
        if seconds and self.hymn_pos + 1 < len(self.hymn_seq):
            self.hymn_job = self.root.after(seconds * 1000, lambda: self.hymn_step(1))

    def _end_hymn(self):
        if getattr(self, "hymn_job", None):
            self.root.after_cancel(self.hymn_job)
        self.hymn_job, self.hymn_live = None, None
        if getattr(self, "hymn_now", None):
            self.hymn_now.config(text="No hymn on screen", fg=MUTED)

    def new_hymn(self):
        self._hymn_editor(None)

    def edit_hymn(self):
        if self.hymn_selected:
            self._hymn_editor(self.media.get_hymn(self.hymn_selected.id))

    def _hymn_editor(self, hymn):
        window = tk.Toplevel(self.root, bg=BG, padx=16, pady=14)
        window.title("Edit hymn" if hymn else "New hymn")
        window.transient(self.root)
        window.geometry("620x620")
        top = tk.Frame(window, bg=BG)
        top.pack(fill="x")
        fields = {}
        for column, (name, width, value) in enumerate((("Number", 6, hymn.number if hymn else ""),
                                                       ("Title", 30, hymn.title if hymn else ""),
                                                       ("Hymn book", 16, hymn.collection if hymn else ""))):
            self._label(top, name).grid(row=0, column=column, sticky="w", padx=(0, 6))
            entry = tk.Entry(top, bg=FIELD, fg=INK, insertbackground=INK, relief="flat", font=self.font(12), width=width)
            entry.insert(0, value)
            entry.grid(row=1, column=column, sticky="ew", padx=(0, 6), ipady=4)
            fields[name] = entry
        top.columnconfigure(1, weight=1)
        self._label(window, "Words: leave an empty line between stanzas. Start the chorus with \"Chorus:\" "
                            "(or Refrain / Egbe).", 10, MUTED, wraplength=580, justify="left").pack(anchor="w",
                                                                                                    pady=(10, 4))
        text = tk.Text(window, bg=FIELD, fg=INK, insertbackground=INK, relief="flat", font=self.font(12), wrap="word",
                       undo=True)
        text.pack(fill="both", expand=True)
        if hymn:
            text.insert("1.0", lyrics_text(hymn.stanzas))
        note = self._label(window, "", 10, GOLD)
        note.pack(anchor="w", pady=(6, 0))

        def save():
            stanzas = parse_lyrics(text.get("1.0", "end"))
            title = fields["Title"].get().strip()
            if not title or not stanzas:
                note.config(text="A hymn needs a title and some words.", fg=RED)
                return
            if hymn:
                self.media.update_hymn(hymn.id, title, stanzas, fields["Number"].get(), fields["Hymn book"].get(),
                                       ordered=False)
                saved_id = hymn.id
            else:
                saved_id = self.media.add_hymn(title, stanzas, fields["Number"].get(), fields["Hymn book"].get())
            window.destroy()
            self._after_hymn_change(saved_id)

        buttons = tk.Frame(window, bg=BG)
        buttons.pack(fill="x", pady=(10, 0))
        for label, color, command in (("SAVE", GREEN, save), ("Cancel", FIELD, window.destroy)):
            tk.Button(buttons, text=label, command=command, bg=color, fg="white", relief="flat", bd=0, padx=16, pady=8,
                      font=self.font(11, "bold"), takefocus=0, cursor="hand2").pack(side="left", padx=(0, 8))
        fields["Title" if not hymn else "Number"].focus_set()
        self.hymn_editor_window = window  # for tests
        self.hymn_editor_fields, self.hymn_editor_text, self.hymn_editor_save = fields, text, save

    def _after_hymn_change(self, hymn_id):
        hymn = self.media.get_hymn(hymn_id)
        if self.hymn_live and self.hymn_live.id == hymn_id and hymn:
            self.hymn_live, self.hymn_seq = hymn, sequence(hymn, self.repeat_chorus.get())
            self.hymn_pos = min(self.hymn_pos, len(self.hymn_seq) - 1)
            self._show_stanza()
        if self.media_kind != "hymn":
            self._pick_kind("hymn")
        self.hymn_selected = hymn
        self._fill_hymn_list()
        if hymn:
            self._show_hymn(hymn)

    def import_hymns(self):
        paths = filedialog.askopenfilenames(parent=self.root, title="Import hymns",
                                            filetypes=[("Hymn files", "*.txt *.TXT *.xml *.XML *.docx *.DOCX *.pdf *.PDF"),
                                                       ("All files", "*")])
        added, problems = self.import_hymn_files(paths)
        if problems:
            messagebox.showwarning("Some hymns were not imported", "\n".join(problems[:10]), parent=self.root)
        if added:
            self.media_status.config(text=f"Imported {added} hymn{'s' if added != 1 else ''}.", fg=GREEN)
            self.media_search.delete(0, "end")
            self._fill_hymn_list()

    def import_hymn_files(self, paths):
        added, problems = 0, []
        for path in paths:
            name = str(path).replace("\\", "/").rsplit("/", 1)[-1]
            try:
                raw = open(path, "rb").read()
                book = name.rsplit(".", 1)[0]
                if name.lower().endswith(".xml"):
                    number, title, collection, stanzas, ordered = parse_openlyrics(raw)
                    found = [(number, title, stanzas, collection, ordered)]
                elif name.lower().endswith((".docx", ".pdf")):  # a whole hymn book: split it into its hymns
                    text = read_docx(path) if name.lower().endswith(".docx") else read_pdf(path)
                    found = [(n, t, s, book, False) for n, t, s in split_hymn_book(text)]
                    if not found:
                        found = [(n, t, s, "", False) for n, t, s in parse_text_file(text, book)]
                else:
                    try:
                        text = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        text = raw.decode("cp1252", errors="replace")
                    found = [(n, t, s, "", False) for n, t, s in parse_text_file(text, book)]
                    if len(found) == 1 and not text.lstrip().startswith("#"):
                        book_hymns = split_hymn_book(text)  # a numbered hymn book saved as text
                        if len(book_hymns) >= 3:
                            found = [(n, t, s, book, False) for n, t, s in book_hymns]
                found = [f for f in found if f[2]]
                if not found:
                    problems.append(f"{name}: no hymn words found.")
                for number, title, stanzas, collection, ordered in found:
                    self.media.add_hymn(title, stanzas, number, collection, ordered)
                    added += 1
            except Exception as error:
                logging.warning("Could not import %s: %s", path, error)
                problems.append(f"{name}: could not be read ({error}).")
        return added, problems

    def delete_hymn(self):
        hymn = self.hymn_selected
        if not hymn or not messagebox.askyesno("Delete", f"Delete the hymn \"{hymn.title}\"?", parent=self.root):
            return
        if self.hymn_live and self.hymn_live.id == hymn.id:
            self.clear_live()
        self.media.delete_hymn(hymn.id)
        self.hymn_selected = None
        self._fill_hymn_list()
        self._show_media(None)

    def _load_hymn_background(self, path):
        self.hymn_backdrop = None
        if path:
            try:
                self.hymn_backdrop = load_picture(path)
            except Exception:
                logging.warning("Hymn background %s could not be opened; using black", path)

    def use_as_hymn_background(self):
        item, picture = self.media_selected, self.media_picture
        if not item or picture is None:
            return
        if str(item.path) == self.settings.get("hymn_background"):  # pressed again: back to black
            self.settings.pop("hymn_background")
            self.hymn_backdrop = None
            self.media_status.config(text="Hymns are back on black.", fg=MUTED)
        else:
            self.settings["hymn_background"] = str(item.path)
            self.hymn_backdrop = picture
            self.media_status.config(text=f"Hymn words now show over \"{item.title}\" (dimmed). Press again to "
                                          "go back to black.", fg=MUTED)
        self._save_settings()
        if self.hymn_live:
            self._show_stanza()

    # ---- music: plays on its own, the screen stays as it is ----
    def _build_music_controls(self, page):
        holder = self.music_bar = tk.Frame(page, bg=FIELD, padx=12, pady=6)
        self.music_now = self._label(holder, "No music loaded", 10, MUTED, "bold", anchor="w")
        self.music_now.pack(fill="x")
        bar = tk.Frame(holder, bg=FIELD)
        bar.pack(fill="x", pady=(4, 0))

        def control(text, command, width=5, color=PANEL):
            button = tk.Button(bar, text=text, command=command, bg=color, fg="white", activebackground=color,
                               activeforeground="white", relief="flat", bd=0, width=width, pady=4,
                               font=self.font(10, "bold"), takefocus=0, cursor="hand2")
            button.pack(side="left", padx=2)
            return button

        control(f"−{SKIP_SECONDS}s", lambda: self._music_do("skip", -SKIP_SECONDS))
        self.music_play = control("▶ PLAY", lambda: self._music_do("toggle"), 9, GREEN)
        control(f"+{SKIP_SECONDS}s", lambda: self._music_do("skip", SKIP_SECONDS))
        control("■ STOP", self.stop_music, 7, RED)
        self.music_time = self._label(bar, "0:00 / 0:00", 10, INK, "bold", width=13)
        self.music_time.pack(side="left", padx=(8, 4))
        self.music_seek = ttk.Scale(bar, from_=0, to=1, orient="horizontal", takefocus=0)
        self.music_seek.pack(side="left", fill="x", expand=True, padx=4)
        self.music_seek.bind("<ButtonPress-1>", lambda _e: setattr(self, "music_seeking", True))
        self.music_seek.bind("<ButtonRelease-1>", self._music_seek_released)
        self.music_repeat = tk.BooleanVar(value=False)
        tk.Checkbutton(bar, text="Repeat", variable=self.music_repeat, bg=FIELD, fg=INK, selectcolor=PANEL,
                       activebackground=FIELD, activeforeground=INK, takefocus=0,
                       command=lambda: self._music_do("set_loop", self.music_repeat.get())).pack(side="left", padx=6)
        self._label(bar, "Vol").pack(side="left")
        self.music_volume = ttk.Scale(bar, from_=0, to=100, orient="horizontal", length=90, takefocus=0,
                                      command=lambda v: self._music_do("set_volume", float(v)))
        level = self.settings.get("music_volume", 70)
        self.music_volume.set(level if isinstance(level, (int, float)) and 0 <= level <= 100 else 70)
        self.music_volume.pack(side="left")

    def _music_player(self):
        if self.music:
            return self.music
        options = {k: v for k, v in self.settings.get("video_player_options", {}).items() if k != "video_output"}
        try:
            self.music = video_module.Player(None, audio_device=self.settings.get("audio_device"), **options)
        except Exception as error:
            logging.exception("Music player could not start")
            self.media_status.config(text="The sound player is not installed on this laptop. Run setup again."
                                     if isinstance(error, OSError) else f"Music player failed: {error}", fg=RED)
            return None
        self.music.set_volume(self.music_volume.get())
        return self.music

    def play_music(self, item):
        if not item.path.exists():
            self.media_status.config(text="That recording is missing. Delete it and add it again.", fg=RED)
            return
        player = self._music_player()
        if not player:
            return
        if not (self.music_item and self.music_item.id == item.id and player.loaded is not None):
            player.load(item.path, play=True, loop=self.music_repeat.get())
            self.music_item = item
            logging.info("Music playing: %s", item.title)
        else:
            player.play()
        if self.media_kind == "audio":
            self._fill_media_list()

    def stop_music(self):
        if self.music:
            self.music.stop()
        self.music_item = None
        if self.media and self.media_kind == "audio":
            self._fill_media_list()

    def _music_do(self, action, *args):
        if action == "set_volume":
            self.settings["music_volume"] = int(float(args[0]))
        if not self.music or self.music.loaded is None:
            if action == "toggle" and self.media_selected and self.media_selected.is_audio:
                self.play_music(self.media_selected)
            return
        getattr(self.music, action)(*args)

    def _music_seek_released(self, _event):
        self.music_seeking = False
        self._music_do("seek_to", self.music_seek.get())

    # ---- the bars under the media page ----
    def _update_service_bars(self):
        if not self.media:
            return
        show_hymn = self.media_kind == "hymn" or bool(self.hymn_live)
        if show_hymn != bool(self.hymn_bar.winfo_ismapped()):
            if show_hymn:
                self.hymn_bar.pack(fill="x", before=self.media_bottom)
            else:
                self.hymn_bar.pack_forget()
        music = self.music
        loaded = bool(music and music.loaded is not None and self.music_item)
        show_music = self.media_kind == "audio" or loaded
        if show_music != bool(self.music_bar.winfo_ismapped()):
            if show_music:
                self.music_bar.pack(fill="x", before=self.media_bottom)
            else:
                self.music_bar.pack_forget()
        if not loaded:
            self.music_now.config(text="No music loaded", fg=MUTED)
            self.music_play.config(text="▶ PLAY")
            return
        position, duration = music.position, music.duration
        state = "ENDED" if music.ended else "PAUSED" if music.paused else "PLAYING"
        self.music_now.config(text=f"♪ MUSIC {state}: {self.music_item.title}"[:110],
                              fg=GREEN if state == "PLAYING" else GOLD)
        self.music_time.config(text=f"{fmt_time(position)} / {fmt_time(duration)}")
        self.music_play.config(text="▶ PLAY" if music.paused else "❚❚ PAUSE")
        if duration and not self.music_seeking:
            self.music_seek.config(to=duration)
            self.music_seek.set(position)

    # ---- sound out: lock the sound to the mixer cable, not the projector's HDMI ----
    def _fill_sound_outputs(self):
        player = self.player or self._music_player()
        devices = player.audio_devices() if player else []
        self.sound_devices = {"Laptop default": "auto"}
        for name, description in devices:
            if name != "auto" and not name.startswith("openal"):  # openal: a second name for the default
                self.sound_devices[description] = name
        self.sound_out["values"] = list(self.sound_devices)

    def _sound_out_picked(self):
        label = self.sound_out.get()
        name = self.sound_devices.get(label, "auto")
        self.settings["audio_device"], self.settings["audio_device_label"] = name, label
        self._save_settings()
        for player in (self.player, self.music):
            if player:
                player.set_audio_device(name)
        self.media_status.config(text=f"Video and music sound now go to: {label}", fg=MUTED)

    # ---- announcement loop: today's posters in turn, before the service ----
    def toggle_loop(self):
        if self.loop_job:
            self._stop_loop()
            self.media_status.config(text="Loop stopped. The last poster stays up.", fg=MUTED)
            return
        self.loop_items = self.media.current_announcements()
        if not self.loop_items:
            self.media_status.config(text="No posters are current today. Check their Show from / until dates.",
                                     fg=GOLD)
            return
        self.loop_pos = 0
        self._loop_step()

    def _loop_step(self):
        self.loop_job = None
        for _ in range(len(self.loop_items)):
            item = self.loop_items[self.loop_pos % len(self.loop_items)]
            self.loop_pos += 1
            try:
                picture = load_picture(item.path)
            except Exception:
                logging.warning("Loop skipped %s: cannot open it", item.path)
                continue
            self.looping_now = True
            try:
                self._take_screen()
                self.projector.show_picture(picture)
                self._set_mode("media", item)
                self.live_media = item
            finally:
                self.looping_now = False
            seconds = self.settings.get("loop_seconds", LOOP_SECONDS)
            seconds = seconds if isinstance(seconds, (int, float)) and 2 <= seconds <= 600 else LOOP_SECONDS
            self.live_note.config(text=f"Looping {len(self.loop_items)} posters, {seconds:g} s each")
            self.loop_button.config(text="STOP LOOP", bg=RED, activebackground=RED)
            self.loop_job = self.root.after(int(seconds * 1000), self._loop_step)
            return
        self.media_status.config(text="None of today's posters could be opened.", fg=RED)
        self._stop_loop()

    def _stop_loop(self):
        if getattr(self, "loop_job", None):
            self.root.after_cancel(self.loop_job)
        self.loop_job = None
        if getattr(self, "loop_button", None):
            self.loop_button.config(text="LOOP TODAY'S POSTERS", bg=BLUE, activebackground=BLUE)

    # ---- service order: prepare the whole service, then GO / NEXT ----
    def _build_order_page(self, page):
        if not self.media:
            self._label(page, self.media_error or "Media library is off.", 13, RED, "bold").pack(padx=20, pady=20)
            return
        main = tk.Frame(page, bg=BG, padx=12, pady=6)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=3, uniform="o")
        main.columnconfigure(1, weight=2, uniform="o")
        main.rowconfigure(0, weight=1)

        left = tk.Frame(main, bg=BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        saved = tk.Frame(left, bg=BG)
        saved.pack(fill="x")
        self._label(saved, "TODAY'S SERVICE ORDER", 10, GOLD, "bold").pack(side="left")
        for text, command in (("DELETE SAVED", self.delete_saved_order), ("SAVE AS", self.save_order_as),
                              ("OPEN", self.open_saved_order)):
            tk.Button(saved, text=text, command=command, bg=FIELD, fg=INK, relief="flat", bd=0, padx=8, pady=3,
                      font=self.font(9, "bold"), takefocus=0, cursor="hand2").pack(side="right", padx=(4, 0))
        self.saved_orders = self._combo(saved, 18, lambda _e: None)
        self.saved_orders.pack(side="right", padx=(8, 2))
        self.order_list = tk.Listbox(left, bg=PANEL, fg=INK, selectbackground=BLUE, relief="flat", bd=0,
                                     highlightthickness=0, font=self.font(13), activestyle="none", takefocus=0,
                                     exportselection=False)
        self.order_list.pack(fill="both", expand=True, pady=(6, 6))
        self.order_list.bind("<Double-Button-1>", lambda _e: self.go_order())
        tools = tk.Frame(left, bg=BG)
        tools.pack(fill="x")
        for text, command in (("▲ UP", lambda: self.move_order(-1)), ("▼ DOWN", lambda: self.move_order(1)),
                              ("REMOVE", self.remove_from_order), ("CLEAR ALL", self.clear_order)):
            tk.Button(tools, text=text, command=command, bg=FIELD, fg=INK, relief="flat", bd=0, padx=10, pady=6,
                      font=self.font(10, "bold"), takefocus=0, cursor="hand2").pack(side="left", padx=(0, 6))

        right = tk.Frame(main, bg=PANEL, padx=12, pady=10)
        right.grid(row=0, column=1, sticky="nsew")
        self._label(right, "ADD TO THE ORDER", 9, BLUE, "bold").pack(anchor="w")
        kinds = tk.Frame(right, bg=PANEL)
        kinds.pack(fill="x", pady=(4, 4))
        self.order_kind_buttons = {}
        for kind, text in (("announcement", "POSTERS"), ("hymn", "HYMNS"), ("video", "VIDEOS"), ("image", "IMAGES"),
                           ("audio", "MUSIC")):
            button = tk.Button(kinds, text=text, command=lambda k=kind: self._pick_order_kind(k), relief="flat", bd=0,
                               padx=6, pady=3, font=self.font(9, "bold"), takefocus=0, cursor="hand2")
            button.pack(side="left", padx=(0, 3))
            self.order_kind_buttons[kind] = button
        self.order_search = tk.Entry(right, bg=FIELD, fg=INK, insertbackground=INK, relief="flat", font=self.font(11))
        self.order_search.pack(fill="x", ipady=3)
        self.order_search.bind("<KeyRelease>", lambda _e: self._fill_order_choices())
        self.order_choice_list = tk.Listbox(right, bg=BG, fg=INK, selectbackground=BLUE, relief="flat", bd=0,
                                            highlightthickness=0, font=self.font(11), activestyle="none", takefocus=0,
                                            exportselection=False)
        self.order_choice_list.pack(fill="both", expand=True, pady=6)
        self.order_choice_list.bind("<Double-Button-1>", lambda _e: self.add_to_order())
        adds = tk.Frame(right, bg=PANEL)
        adds.pack(fill="x")
        tk.Button(adds, text="ADD ◀", command=self.add_to_order, bg=BLUE, fg="white", relief="flat", bd=0, padx=12,
                  pady=6, font=self.font(10, "bold"), takefocus=0, cursor="hand2").pack(side="left")
        tk.Button(adds, text="+ SERMON TIME", command=lambda: self.add_to_order(("scripture", 0)), bg=FIELD, fg=INK,
                  relief="flat", bd=0, padx=10, pady=6, font=self.font(10, "bold"), takefocus=0,
                  cursor="hand2").pack(side="left", padx=6)

        bottom = tk.Frame(page, bg=PANEL, padx=12, pady=10)
        bottom.pack(fill="x")
        self.order_status = self._label(bottom, "Build the order before the service; during it, NEXT ▶ puts up "
                                                "each item in turn.", 10, MUTED, wraplength=430, justify="left")
        self.order_status.pack(side="left")
        self._button(bottom, "NEXT ▶", BLUE, self.next_order, 9).pack(side="right")
        self._button(bottom, "GO LIVE (Space)", GREEN, self.go_order, 15).pack(side="right", padx=8)
        self._button(bottom, "◀ BACK", FIELD, lambda: self.next_order(-1), 8).pack(side="right")
        self._pick_order_kind("announcement")

    def _pick_order_kind(self, kind):
        self.order_kind = kind
        for other, button in self.order_kind_buttons.items():
            color, ink = (BLUE, "white") if other == kind else (FIELD, INK)
            button.config(bg=color, fg=ink, activebackground=color, activeforeground=ink)
        self._fill_order_choices()

    def _fill_order_choices(self):
        words = self.order_search.get()
        if self.order_kind == "hymn":
            self.order_choices = [("hymn", h.id, h.label) for h in self.media.hymns(words)]
        else:
            self.order_choices = [("media", i.id, i.label) for i in self.media.items(self.order_kind, words)]
        self.order_choice_list.delete(0, "end")
        for _kind, _id, label in self.order_choices:
            self.order_choice_list.insert("end", label)

    def _entry_label(self, entry):
        kind, item_id = entry
        if kind == "scripture":
            return "[Sermon]  Bible / scripture time"
        if kind == "hymn":
            hymn = self.media.get_hymn(item_id)
            return f"[Hymn]  {hymn.label}" if hymn else "[Hymn]  (deleted)"
        item = self.media.get(item_id)
        if not item:
            return "(deleted)"
        tag = "Video" if item.is_video and item.kind == "announcement" else ORDER_TAGS.get(item.kind, "Media")
        return f"[{tag}]  {item.title}"

    def _fill_order(self):
        if not self.media:
            return
        self.saved_orders["values"] = self.media.playlists()
        chosen = self.order_list.curselection()
        self.order_list.delete(0, "end")
        for row, entry in enumerate(self.order):
            mark = "● " if row == self.order_live else "   "
            self.order_list.insert("end", f"{mark}{row + 1}. {self._entry_label(entry)}")
        if chosen and chosen[0] < len(self.order):
            self.order_list.selection_set(chosen[0])
        elif self.order:
            self.order_list.selection_set(min(self.order_live + 1 if self.order_live is not None else 0,
                                              len(self.order) - 1))

    def _order_changed(self):
        try:
            self.media.save_playlist(CURRENT_ORDER, self.order)  # a restart mid-service loses nothing
        except Exception:
            logging.exception("Could not save the service order")
        self._fill_order()

    def add_to_order(self, entry=None):
        if entry is None:
            chosen = self.order_choice_list.curselection()
            if not chosen:
                self.order_status.config(text="Pick something on the right first.", fg=GOLD)
                return
            kind, item_id, _label = self.order_choices[chosen[0]]
            entry = (kind, item_id)
        selected = self.order_list.curselection()
        where = selected[0] + 1 if selected else len(self.order)
        self.order.insert(where, entry)
        if self.order_live is not None and where <= self.order_live:
            self.order_live += 1
        self._order_changed()
        self.order_list.selection_clear(0, "end")
        self.order_list.selection_set(where)

    def move_order(self, delta):
        chosen = self.order_list.curselection()
        if not chosen:
            return
        row, target = chosen[0], chosen[0] + delta
        if not 0 <= target < len(self.order):
            return
        self.order[row], self.order[target] = self.order[target], self.order[row]
        if self.order_live in (row, target):
            self.order_live = target if self.order_live == row else row
        self._order_changed()
        self.order_list.selection_clear(0, "end")
        self.order_list.selection_set(target)

    def remove_from_order(self):
        chosen = self.order_list.curselection()
        if not chosen:
            return
        row = chosen[0]
        del self.order[row]
        if self.order_live == row:
            self.order_live = None
        elif self.order_live is not None and row < self.order_live:
            self.order_live -= 1
        self._order_changed()
        if self.order:
            self.order_list.selection_clear(0, "end")
            self.order_list.selection_set(min(row, len(self.order) - 1))

    def clear_order(self):
        if self.order and messagebox.askyesno("Clear", "Remove everything from today's order?", parent=self.root):
            self.order, self.order_live = [], None
            self._order_changed()

    def save_order_as(self):
        from tkinter import simpledialog

        name = simpledialog.askstring("Save service order", "Name (e.g. Sunday first service):", parent=self.root)
        if not name or not name.strip():
            return
        try:
            self.media.save_playlist(name, self.order)
        except ValueError as error:
            self.order_status.config(text=str(error), fg=RED)
            return
        self.saved_orders.set(name.strip())
        self._fill_order()
        self.order_status.config(text=f"Saved as \"{name.strip()}\". OPEN it any week to start from it.", fg=GREEN)

    def open_saved_order(self):
        name = self.saved_orders.get()
        if not name:
            self.order_status.config(text="Pick a saved order in the box first.", fg=GOLD)
            return
        if self.order and not messagebox.askyesno("Open", f"Replace today's order with \"{name}\"?", parent=self.root):
            return
        self.order, self.order_live = self.media.playlist(name), None
        self._order_changed()
        self.order_status.config(text=f"Opened \"{name}\".", fg=GREEN)

    def delete_saved_order(self):
        name = self.saved_orders.get()
        if name and messagebox.askyesno("Delete", f"Delete the saved order \"{name}\"?", parent=self.root):
            self.media.delete_playlist(name)
            self.saved_orders.set("")
            self._fill_order()

    def go_order(self, row=None):
        if row is None:
            chosen = self.order_list.curselection()
            if not chosen:
                self.order_status.config(text="Pick an item in the order first.", fg=GOLD)
                return
            row = chosen[0]
        kind, item_id = self.order[row]
        if not self._go_entry(kind, item_id):
            return
        self.order_live = row
        self._fill_order()
        self.order_list.selection_clear(0, "end")
        self.order_list.selection_set(min(row + 1, len(self.order) - 1))  # NEXT is ready
        self.order_list.see(row)

    def next_order(self, delta=1):
        if not self.order:
            return
        row = (self.order_live + delta) if self.order_live is not None else 0
        if not 0 <= row < len(self.order):
            self.order_status.config(text="That was the last item." if delta > 0 else "This is the first item.",
                                     fg=GOLD)
            return
        self.go_order(row)

    def _go_entry(self, kind, item_id):
        """Put one item of the order up, exactly as the media page would."""
        if kind == "scripture":
            self.clear_live()
            self.live_note.config(text="Sermon time: verses go up as the pastor reads them")
            self.order_status.config(text="Sermon time. The Bible page follows the pastor; NEXT ▶ when it ends.",
                                     fg=GREEN)
            return True
        if kind == "hymn":
            hymn = self.media.get_hymn(item_id)
            if not hymn:
                self.order_status.config(text="That hymn was deleted from the library.", fg=RED)
                return False
            self.hymn_selected = hymn
            self.push_hymn(hymn, start=0)
            self.order_status.config(text=f"{hymn.title}: ◀ ▶ arrow keys or the MEDIA page change the stanza.",
                                     fg=GREEN)
            return self.mode == "hymn"
        item = self.media.get(item_id)
        if not item:
            self.order_status.config(text="That item was deleted from the library.", fg=RED)
            return False
        self.media_selected = item
        if item.is_audio:
            self.play_music(item)
            self.order_status.config(text=f"♪ {item.title} playing. The screen stays as it is.", fg=GREEN)
            return True
        if item.is_video or item.is_animated:
            self.get_ready(then_play=True)  # a link may take a moment to find
            self.order_status.config(text=f"{item.title}: playing (a link can take up to 30 s to start). "
                                          "Controls are on the MEDIA page.", fg=GREEN)
            return True
        try:
            picture = load_picture(item.path)
        except Exception:
            self.order_status.config(text=f"\"{item.title}\" cannot be opened (missing or damaged).", fg=RED)
            return False
        self._take_screen()
        self.projector.show_picture(picture)
        self._set_mode("media", item)
        self.live_media = item
        self.order_status.config(text=f"{item.title} is up.", fg=GREEN)
        return True
