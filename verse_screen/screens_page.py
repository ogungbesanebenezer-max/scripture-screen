"""SCREENS (2026-11-21): a second full screen, the stage screen and the livestream link.

- Second full screen: a church with a projector AND a TV gets the same picture full screen on both (verses, hymns,
  posters, black screen, video). Pick the TV once; it comes back by itself when the cable is plugged in again.
- Stage screen: a page any TV, tablet or phone at the front opens (QR code): what is on screen, what is next,
  the time, and a note from the media desk ("5 minutes", "Wrap up").
- Livestream: only for churches that stream with OBS or vMix. A link they add as a Browser source.

Mixed into App, like media_page.
"""
import io
import logging
import tkinter as tk
import webbrowser
from tkinter import ttk

from PIL import Image, ImageTk

from .outputs import Outputs, new_token
from .phone_mic import qr_png
from .style import BG, BLUE, FIELD, GOLD, GREEN, INK, MUTED, RED

OFF = "Off (projector only)"


class ScreensControl:
    def _init_screens(self):
        if not self.settings.get("outputs_token"):
            self.settings["outputs_token"] = new_token()  # kept, so OBS and the stage tablet keep working
        self.outputs = Outputs(self.settings["outputs_token"])
        self.screens_window = None
        self.outputs.screen("standby", standby=self.projector.standby)
        self.projector.outputs = self.outputs
        if not self.outputs.start():
            logging.warning("Stage and livestream pages are off: %s", self.outputs.error)

    # ---- second full screen ----
    def _place_second(self):
        """Called whenever the screens are (re)listed: the TV follows its cable being plugged in or out."""
        wanted = self.settings.get("second_display", "")
        main = self.display_box.get()
        display = next((d for d in self.displays if d.label == wanted and d.label != main), None)
        if display is None or len(self.displays) < 2:
            self._remove_second()
            return
        mirror = self.projector.mirror
        if mirror is None:
            from .app import Projector  # late: app imports this module
            mirror = Projector(self.root, self.projector.standby, name="Scripture Display 2")
            self.projector.copy_to(mirror)
            self.projector.mirror = mirror
            logging.info("Second full screen on: %s", display.label)
        mirror.place(display, is_only_screen=False)
        self.root.focus_force()

    def _remove_second(self):
        mirror = self.projector.mirror
        if mirror is None:
            return
        self.projector.mirror = None
        try:
            mirror.window.destroy()
        except tk.TclError:
            pass
        logging.info("Second full screen off")

    def _second_picked(self, _event=None):
        choice = self.second_box.get()
        self.settings["second_display"] = "" if choice == OFF else choice
        self._save_settings()
        self._place_second()
        self._fill_screens_window()

    # ---- what the stage screen shows as NEXT ----
    def _update_next(self):
        outputs = getattr(self, "outputs", None)
        if not outputs:
            return
        try:
            if self.mode == "hymn" and self.hymn_live and self.hymn_pos + 1 < len(self.hymn_seq):
                stanza = self.hymn_seq[self.hymn_pos + 1]
                outputs.next_up(f"{self.hymn_live.title} · {stanza.label}", stanza.text)
            elif self.preview_ref and (self.preview_ref, self.preview_code) != self._live_state():
                outputs.next_up(*self._verse(self.preview_ref, self.preview_code))
            else:
                outputs.next_up()
        except Exception:
            logging.exception("Stage NEXT line failed")

    def send_stage_note(self, text=None):
        text = self.stage_note.get() if text is None else text
        self.outputs.note(text)
        logging.info("Stage note: %s", text or "(cleared)")
        if self.screens_window and self.screens_window.winfo_exists():
            self.stage_status.config(text=f"On the stage screen: {text}" if text else "No note on the stage screen.",
                                     fg=GREEN if text else MUTED)

    # ---- the window ----
    def show_screens(self):
        if self.screens_window and self.screens_window.winfo_exists():
            self._fill_screens_window()
            self.screens_window.lift()
            return
        self.screens_window = tk.Toplevel(self.root, bg=BG, padx=20, pady=14)
        self.screens_window.title("Screens: TV, stage, livestream")
        self.screens_window.transient(self.root)
        self._fill_screens_window()

    def _heading(self, parent, text):
        self._label(parent, text, 12, GOLD, "bold").pack(anchor="w", pady=(12, 2))

    def _copy(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.stream_status.config(text="Link copied. In OBS: Sources, +, Browser, paste it as the URL.", fg=GREEN)

    def _fill_screens_window(self):
        window = self.screens_window
        if not window or not window.winfo_exists():
            return
        for child in window.winfo_children():
            child.destroy()

        self._heading(window, "SECOND FULL SCREEN (projector + TV)")
        row = tk.Frame(window, bg=BG)
        row.pack(fill="x")
        main = self.display_box.get()
        others = [d.label for d in self.displays if d.label != main]
        self.second_box = ttk.Combobox(row, state="readonly", width=44, takefocus=0, values=[OFF, *others])
        wanted = self.settings.get("second_display", "")
        self.second_box.set(wanted if wanted in others else OFF)
        self.second_box.bind("<<ComboboxSelected>>", self._second_picked)
        self.second_box.pack(side="left")
        if self.projector.mirror:
            note, color = "● ON: both screens show the same thing, full screen.", GREEN
        elif wanted:
            note, color = "That screen is not connected now. It comes back by itself when it is plugged in.", GOLD
        elif len(self.displays) < 3:
            note, color = ("Plug in the TV as well, then pick it here. (Windows: Win+P, Extend.) "
                           f"{len(self.displays)} screen(s) found now.", MUTED)
        else:
            note, color = "Pick the TV to show everything on it too.", MUTED
        self._label(window, note, 10, color, wraplength=560, justify="left").pack(anchor="w", pady=(4, 0))
        tk.Button(window, text="Rescan screens", command=lambda: (self._refresh_displays(), self._fill_screens_window()),
                  bg=FIELD, fg=INK, relief="flat", bd=0, padx=10, pady=3, takefocus=0).pack(anchor="w", pady=(4, 0))

        self._heading(window, "STAGE SCREEN (for the pastor and singers)")
        if not self.outputs.port:
            self._label(window, self.outputs.error or "The stage page is not running.", 11, RED, "bold").pack(anchor="w")
            return
        links = self.outputs.network_links("stage")
        stage = tk.Frame(window, bg=BG)
        stage.pack(fill="x")
        if links:
            image = ImageTk.PhotoImage(Image.open(io.BytesIO(qr_png(links[0], scale=4))))
            holder = tk.Label(stage, image=image, bg="white")
            holder.image = image
            holder.pack(side="left", padx=(0, 12))
        text = tk.Frame(stage, bg=BG)
        text.pack(side="left", fill="x")
        if links:
            self._label(text, "Scan with the tablet or phone at the front, or type this in a TV's browser:", 10, INK,
                        wraplength=380, justify="left").pack(anchor="w")
            self._label(text, links[0], 10, MUTED, wraplength=380, justify="left").pack(anchor="w", pady=(2, 6))
            self._label(text, "It must be on the same Wi-Fi or hotspot as this laptop.", 9, MUTED).pack(anchor="w")
        else:
            self._label(text, "No network yet: turn on the hotspot or Wi-Fi, then reopen this window.", 10, GOLD,
                        wraplength=380, justify="left").pack(anchor="w")
        tk.Button(text, text="Open it on this laptop", command=lambda: webbrowser.open(self.outputs.link("stage")),
                  bg=FIELD, fg=INK, relief="flat", bd=0, padx=10, pady=3, takefocus=0).pack(anchor="w", pady=(6, 0))
        note_row = tk.Frame(window, bg=BG)
        note_row.pack(fill="x", pady=(10, 0))
        self._label(note_row, "Note to the pastor").pack(side="left")
        self.stage_note = tk.Entry(note_row, bg=FIELD, fg=INK, insertbackground=INK, relief="flat", font=self.font(12),
                                   width=26)
        self.stage_note.insert(0, self.outputs.state.get("note", ""))
        self.stage_note.pack(side="left", padx=6, ipady=3)
        self.stage_note.bind("<Return>", lambda _e: self.send_stage_note())
        for label in ("5 minutes", "Wrap up"):
            tk.Button(note_row, text=label, command=lambda t=label: (self.stage_note.delete(0, "end"),
                                                                     self.stage_note.insert(0, t), self.send_stage_note()),
                      bg=FIELD, fg=INK, relief="flat", bd=0, padx=8, pady=3, takefocus=0).pack(side="left", padx=2)
        tk.Button(note_row, text="SEND", command=self.send_stage_note, bg=GREEN, fg="white", relief="flat", bd=0,
                  padx=10, pady=3, takefocus=0).pack(side="left", padx=(6, 2))
        tk.Button(note_row, text="CLEAR", command=lambda: (self.stage_note.delete(0, "end"), self.send_stage_note("")),
                  bg=RED, fg="white", relief="flat", bd=0, padx=10, pady=3, takefocus=0).pack(side="left")
        current = self.outputs.state.get("note", "")
        self.stage_status = self._label(window, f"On the stage screen: {current}" if current else "", 10, GREEN)
        self.stage_status.pack(anchor="w", pady=(4, 0))

        self._heading(window, "LIVESTREAM (only if you stream with OBS or vMix)")
        self._label(window, "In OBS or vMix add a Browser source, size 1920 x 1080, with one of these links:", 10, INK,
                    wraplength=560, justify="left").pack(anchor="w")
        for name, link in (("Lower third over the camera", self.outputs.link("live")),
                           ("Full screen, like the projector", self.outputs.link("live", style="full"))):
            line = tk.Frame(window, bg=BG)
            line.pack(fill="x", pady=2)
            self._label(line, name, 10, INK, width=28, anchor="w").pack(side="left")
            tk.Button(line, text="Copy link", command=lambda l=link: self._copy(l), bg=BLUE, fg="white", relief="flat",
                      bd=0, padx=10, pady=2, takefocus=0).pack(side="left")
        self.stream_status = self._label(window, "OBS on another computer: use the stage link above with /live "
                                         "in place of /stage.", 9, MUTED, wraplength=560, justify="left")
        self.stream_status.pack(anchor="w", pady=(4, 0))
