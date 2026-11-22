"""OUR CHURCH: the church's name, logo and colour, set once and used on every screen.

The logo shows full size on the standby screen (unless the operator picked another standby picture), small and
faint in a corner of verses and hymns, and on the livestream lower third. The colour is used for verse and hymn
titles. Mixed into App; the logo file is copied into media/church/ so the USB stick it came from can go.
"""
import logging
import shutil
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog, ttk

from PIL import Image, ImageTk

from .bible import ROOT
from .media import PICTURE_TYPES, load_picture
from . import media as media_module
from .style import BG, BLUE, FIELD, GOLD, GREEN, INK, MUTED, PANEL, RED

CORNERS = {"Bottom right": "bottom-right", "Bottom left": "bottom-left", "Top right": "top-right",
           "Top left": "top-left", "Off": "off"}
STANDBY_DEFAULT = ROOT / "data" / "standby.jpg"
COLORS = ["#e6b85c", "#ffffff", "#7fb2ff", "#6fd39a", "#ff8a7a", "#c9a2ff", "#ffd400", "#ff9f43"]


def church_folder():
    return Path(media_module.MEDIA_DIR) / "church"


def open_logo(path):
    """The logo with its see-through parts kept (for the corner); None if it cannot be read."""
    try:
        with Image.open(path) as opened:
            opened.load()
            return opened.convert("RGBA")
    except Exception:
        logging.warning("Church logo %s could not be opened", path)
        return None


class ChurchControl:
    def apply_church(self):
        """Put the saved name, colour and logo on the screens. Safe to call at any time."""
        settings = self.settings
        logo_path = settings.get("church_logo")
        logo = open_logo(logo_path) if logo_path else None
        accent = settings.get("church_color") or GOLD
        corner = settings.get("church_logo_corner", "bottom-right")
        self.projector.set_brand(accent, logo, corner)
        if logo is not None and not settings.get("standby_image"):
            try:
                self.projector.set_standby(load_picture(logo_path))
            except Exception:
                logging.warning("Church logo could not be used as the standby picture")
        if getattr(self, "outputs", None):
            self.outputs.brand(settings.get("church_name", ""), settings.get("church_color", ""), logo)
        name = settings.get("church_name", "").strip()
        self.root.title(f"Scripture Screen - {name}" if name else "Scripture Screen - Operator")

    def show_church(self):
        window = tk.Toplevel(self.root, bg=BG, padx=18, pady=14)
        window.title("Our church")
        window.transient(self.root)
        window.geometry("1000x560")
        chosen = {"logo": self.settings.get("church_logo") or "", "color": self.settings.get("church_color") or GOLD}

        self._label(window, "Church name", 10, MUTED).pack(anchor="w")
        name = tk.Entry(window, bg=FIELD, fg=INK, insertbackground=INK, relief="flat", font=self.font(14))
        name.insert(0, self.settings.get("church_name", ""))
        name.pack(fill="x", ipady=5, pady=(2, 12))

        middle = tk.Frame(window, bg=BG)
        middle.pack(fill="both", expand=True)
        left = tk.Frame(middle, bg=BG)
        left.pack(side="left", fill="y", padx=(0, 16))
        self._label(left, "Logo", 10, MUTED).pack(anchor="w")
        logo_note = self._label(left, "", 10, MUTED, wraplength=230, justify="left")
        buttons = tk.Frame(left, bg=BG)
        buttons.pack(anchor="w", pady=(2, 4))
        self._label(left, "Title colour", 10, MUTED).pack(anchor="w", pady=(14, 2))
        swatches = tk.Frame(left, bg=BG)
        swatches.pack(anchor="w")
        self._label(left, "Logo on verses and hymns", 10, MUTED).pack(anchor="w", pady=(14, 2))
        corner = ttk.Combobox(left, values=list(CORNERS), state="readonly", width=16, font=self.font(11))
        saved_corner = self.settings.get("church_logo_corner", "bottom-right")
        corner.set(next((k for k, v in CORNERS.items() if v == saved_corner), "Bottom right"))
        corner.pack(anchor="w")
        logo_note.pack(anchor="w", pady=(10, 0))

        self._label(middle, "How the screen will look", 10, MUTED).pack(anchor="w")
        preview = tk.Canvas(middle, bg="black", highlightthickness=1, highlightbackground=PANEL, width=420, height=236)
        preview.pack(anchor="w", pady=(2, 0))
        status = self._label(window, "", 10, MUTED)
        status.pack(anchor="w", pady=(8, 0))
        keep = {}

        def draw(_event=None):
            preview.delete("all")
            width, height = 420, 236
            preview.create_text(width / 2, 26, text="John 3:16 (KJV)", fill=chosen["color"],
                                font=("Georgia", -17, "bold"))
            preview.create_text(width / 2, 130, text="For God so loved the world, that he gave his only begotten "
                                "Son, that whosoever believeth in him should not perish, but have everlasting life.",
                                fill="white", width=360, justify="center", font=("Georgia", -15))
            logo = open_logo(chosen["logo"]) if chosen["logo"] else None
            where = CORNERS[corner.get()]
            if logo is not None and where != "off":
                small = logo.resize((max(1, round(logo.width * (height // 9) / logo.height)), height // 9))
                small.putalpha(small.getchannel("A").point(lambda v: int(v * 0.55)))
                keep["mark"] = ImageTk.PhotoImage(small, master=preview)
                margin = 8
                x = width - margin - small.width / 2 if "right" in where else margin + small.width / 2
                y = height - margin - small.height / 2 if "bottom" in where else margin + small.height / 2
                preview.create_image(x, y, image=keep["mark"])
            logo_note.config(text=("The logo also fills the screen before service and between items, unless you "
                                   "chose another standby picture in Media.") if logo is not None else
                             "No logo yet. PNG with a see-through background looks best.")

        def pick_logo():
            path = filedialog.askopenfilename(parent=window, title="Choose your church logo",
                                              filetypes=[("Pictures", " ".join(f"*{t} *{t.upper()}" for t in
                                                                               PICTURE_TYPES)), ("All files", "*")])
            if not path:
                return
            if open_logo(path) is None:
                status.config(text="That file could not be opened as a picture.", fg=RED)
                return
            chosen["logo"] = path
            draw()

        def remove_logo():
            chosen["logo"] = ""
            draw()

        def pick_color(color=None):
            if color is None:
                color = colorchooser.askcolor(color=chosen["color"], parent=window, title="Title colour")[1]
            if color:
                chosen["color"] = color
                draw()

        for text, command, color in (("CHOOSE LOGO", pick_logo, BLUE), ("REMOVE", remove_logo, FIELD)):
            tk.Button(buttons, text=text, command=command, bg=color, fg="white", relief="flat", bd=0, padx=12, pady=6,
                      font=self.font(10, "bold"), takefocus=0, cursor="hand2").pack(side="left", padx=(0, 6))
        for color in COLORS:
            tk.Button(swatches, bg=color, activebackground=color, width=2, relief="flat", bd=0, takefocus=0,
                      cursor="hand2", command=lambda c=color: pick_color(c)).pack(side="left", padx=(0, 4), ipady=4)
        tk.Button(swatches, text="Other...", command=pick_color, bg=FIELD, fg=INK, relief="flat", bd=0, padx=8,
                  font=self.font(9), takefocus=0, cursor="hand2").pack(side="left", padx=(4, 0))
        corner.bind("<<ComboboxSelected>>", draw)

        def save():
            self.settings["church_name"] = " ".join(name.get().split())
            self.settings["church_color"] = chosen["color"]
            self.settings["church_logo_corner"] = CORNERS[corner.get()]
            had_logo = self.settings.get("church_logo")
            if chosen["logo"] and chosen["logo"] != had_logo:
                try:
                    folder = church_folder()
                    folder.mkdir(parents=True, exist_ok=True)
                    for old in folder.glob("logo.*"):
                        old.unlink()
                    copy = folder / f"logo{Path(chosen['logo']).suffix.lower() or '.png'}"
                    shutil.copyfile(chosen["logo"], copy)
                    self.settings["church_logo"] = str(copy)
                except OSError as error:
                    status.config(text=f"The logo could not be saved: {error}", fg=RED)
                    return
            elif not chosen["logo"]:
                self.settings.pop("church_logo", None)
                if had_logo and not self.settings.get("standby_image"):
                    self.projector.set_standby(Image.open(STANDBY_DEFAULT).convert("RGB")
                                               if STANDBY_DEFAULT.exists() else None)
            self._save_settings()
            self.apply_church()
            window.destroy()
            self.status.config(text=f"Saved: {self.settings['church_name'] or 'your church'}.")

        bottom = tk.Frame(window, bg=BG)
        bottom.pack(fill="x", pady=(12, 0))
        for text, color, command in (("SAVE", GREEN, save), ("Cancel", FIELD, window.destroy)):
            tk.Button(bottom, text=text, command=command, bg=color, fg="white", relief="flat", bd=0, padx=16, pady=8,
                      font=self.font(11, "bold"), takefocus=0, cursor="hand2").pack(side="left", padx=(0, 8))
        draw()
        name.focus_set()
        self.church_window = window  # for tests
        self.church_name_entry, self.church_save, self.church_corner = name, save, corner
        self.church_choose = chosen
