"""FIND HYMN window: search the free hymns that come with the app and, with internet, Wikisource; see the words;
add one to the church's own hymns. Mixed into App through ServiceControl."""
import logging
import threading
import tkinter as tk

from .hymn_search import search_library, search_online
from .style import BG, BLUE, FIELD, GOLD, GREEN, INK, MUTED, PANEL, RED

FREE_NOTE = ("Only free (public-domain) hymns are found here. For modern worship songs, type or import them "
             "under your church's own song licence.")


class HymnFinder:
    def find_hymn(self):
        window = tk.Toplevel(self.root, bg=BG, padx=16, pady=14)
        window.title("Find a hymn")
        window.transient(self.root)
        window.geometry("860x620")
        top = tk.Frame(window, bg=BG)
        top.pack(fill="x")
        self._label(top, "Hymn title or a line from it", 10, MUTED).pack(anchor="w")
        row = tk.Frame(top, bg=BG)
        row.pack(fill="x", pady=(2, 4))
        query = tk.Entry(row, bg=FIELD, fg=INK, insertbackground=INK, relief="flat", font=self.font(13))
        query.pack(side="left", fill="x", expand=True, ipady=5)
        online = tk.BooleanVar(value=True)
        status = self._label(window, FREE_NOTE, 10, MUTED, wraplength=820, justify="left")
        body = tk.Frame(window, bg=BG)
        results = tk.Listbox(body, bg=PANEL, fg=INK, selectbackground=BLUE, relief="flat", bd=0, highlightthickness=0,
                             font=self.font(11), activestyle="none", exportselection=False, width=44)
        words = tk.Text(body, bg="black", fg=INK, relief="flat", font=self.font(12), wrap="word", padx=12, pady=10)
        found_now, searching = [], {"id": 0}

        def show_words(_event=None):
            chosen = results.curselection()
            words.config(state="normal")
            words.delete("1.0", "end")
            if chosen:
                hymn = found_now[chosen[0]]
                for stanza in hymn.stanzas:
                    words.insert("end", stanza.label + "\n", "label")
                    words.insert("end", stanza.text + "\n\n")
            words.tag_config("label", foreground=GOLD)
            words.config(state="disabled")

        def fill(hymns, note, color=MUTED):
            found_now[:] = hymns
            results.delete(0, "end")
            for hymn in hymns:
                results.insert("end", hymn.label)
            status.config(text=note, fg=color)
            if hymns:
                results.selection_set(0)
            show_words()

        def search(_event=None):
            text = query.get().strip()
            if not text:
                return
            searching["id"] += 1
            ticket = searching["id"]
            local = search_library(text)
            note = f"{len(local)} in the free hymn library." if local else "Not in the free hymn library."
            if not online.get():
                fill(local, note + "  " + FREE_NOTE)
                return
            fill(local, note + "  Searching the internet too...", GOLD)

            answer = {}

            def look():  # internet search on its own thread; the window picks the answer up below
                try:
                    answer["found"] = search_online(text)
                except OSError as error:
                    logging.info("Online hymn search failed: %s", error)
                    answer["error"] = error

            def collect():
                if ticket != searching["id"] or not window.winfo_exists():
                    return  # a newer search, or the window was closed
                if not answer:
                    window.after(150, collect)
                    return
                have = {h.title.lower() for h in local}
                more = [h for h in answer.get("found", []) if h.title.lower() not in have]
                if "error" in answer:
                    tail = "No internet, or the hymn site is busy: only the free library was searched."
                else:
                    tail = f"{len(more)} more found on the internet." if more else "Nothing more on the internet."
                fill(local + more, f"{note}  {tail}", MUTED if (local or more) else RED)

            threading.Thread(target=look, daemon=True).start()
            window.after(150, collect)

        def add():
            chosen = results.curselection()
            if not chosen:
                status.config(text="Pick a hymn in the list first.", fg=RED)
                return
            hymn = found_now[chosen[0]]
            hymn_id = self.media.add_hymn(hymn.title, hymn.stanzas)
            status.config(text=f"Added \"{hymn.title}\" to your hymns. EDIT it there to set your hymn number.",
                          fg=GREEN)
            self._after_hymn_change(hymn_id)

        tk.Button(row, text="SEARCH", command=search, bg=BLUE, fg="white", relief="flat", bd=0, padx=14, pady=6,
                  font=self.font(11, "bold"), takefocus=0, cursor="hand2").pack(side="left", padx=(8, 0))
        tk.Checkbutton(top, text="Also search the internet (Wikisource, free hymns)", variable=online, bg=BG, fg=INK,
                       selectcolor=FIELD, activebackground=BG, activeforeground=INK, font=self.font(10),
                       takefocus=0).pack(anchor="w")
        status.pack(anchor="w", pady=(4, 6))
        body.pack(fill="both", expand=True)
        results.pack(side="left", fill="y")
        words.pack(side="left", fill="both", expand=True, padx=(10, 0))
        results.bind("<<ListboxSelect>>", show_words)
        results.bind("<Double-Button-1>", lambda _e: add())
        query.bind("<Return>", search)
        buttons = tk.Frame(window, bg=BG)
        buttons.pack(fill="x", pady=(10, 0))
        for label, color, command in (("ADD TO MY HYMNS", GREEN, add), ("Close", FIELD, window.destroy)):
            tk.Button(buttons, text=label, command=command, bg=color, fg="white", relief="flat", bd=0, padx=16,
                      pady=8, font=self.font(11, "bold"), takefocus=0, cursor="hand2").pack(side="left", padx=(0, 8))
        query.focus_set()
        self.hymn_find_window = window  # for tests
        self.hymn_find_query, self.hymn_find_search, self.hymn_find_add = query, search, add
        self.hymn_find_results, self.hymn_find_online = results, online
