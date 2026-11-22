"""Livestream and stage outputs (2026-11-21): what the projector shows, served as web pages on the laptop.

- /live   for OBS or vMix (add it as a Browser source): the verse or hymn as a lower third over the camera
          (see-through background), or ?style=full for a whole black screen like the projector.
- /stage  for the pastor or the singers: a TV, tablet, phone or second laptop at the front opens it in a browser.
          It shows what is on screen now, what the operator has ready next, the time and a note from the media desk.

No internet and no extra program: the laptop serves the pages itself. Plain http (OBS refuses a home-made
certificate), on the same network as the phone mic. A key in the link keeps other people on the Wi-Fi out.
Nothing here can stop the projector: if this fails, the verses still go up as before.
"""
import io
import json
import logging
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from .phone_mic import ExclusiveServer, laptop_addresses

PORTS = (8780, 8781, 8782, 8783)
WAIT_SECONDS = 20  # a page asks "anything new?" and the laptop answers as soon as something changes, or after this
MAX_NOTE = 200


class Outputs:
    """Holds what is on screen now; the Tk thread writes, the web pages read."""

    def __init__(self, token):
        self.token = token
        self.changed = threading.Condition()
        self.seq = 0
        self.state = {"mode": "standby", "title": "", "body": "", "second": "", "standby": "",
                      "next_title": "", "next_body": "", "note": "", "since": time.time(),
                      "church": "", "accent": "", "logo": 0}
        self.picture, self.picture_jpeg = None, None
        self.logo_png = None  # Our Church logo for the livestream lower third
        self.server, self.port, self.error = None, None, None

    # ---- written from the Tk thread ----
    def _update(self, **values):
        with self.changed:
            if all(self.state.get(k) == v for k, v in values.items()):
                return
            if "mode" in values or "title" in values or "body" in values:
                values.setdefault("since", time.time())
            self.state.update(values)
            self.seq += 1
            self.changed.notify_all()

    def screen(self, mode, title="", body="", second="", picture=None, standby=""):
        """mode: text, picture, video, black or standby."""
        with self.changed:
            if picture is not self.picture:
                self.picture, self.picture_jpeg = picture, None
                self.seq += 1  # same words, new poster: the pages still have to fetch it
        self._update(mode=mode, title=title, body=body, second=second or "", standby=standby)

    def brand(self, church="", accent="", logo=None):
        """Our Church: name, colour and logo (a PIL image or None) for the livestream and stage pages."""
        png = None
        if logo is not None:
            image = logo.convert("RGBA")
            image.thumbnail((400, 400))
            out = io.BytesIO()
            image.save(out, "PNG")
            png = out.getvalue()
        with self.changed:
            self.logo_png = png
        self._update(church=church or "", accent=accent or "", logo=(self.state.get("logo", 0) + 1) if png else 0)

    def next_up(self, title="", body=""):
        self._update(next_title=title, next_body=body)

    def note(self, text):
        self._update(note=" ".join(text.split())[:MAX_NOTE])

    # ---- read from the web server threads ----
    def snapshot(self, since=None, wait=WAIT_SECONDS):
        with self.changed:
            if since is not None and since == self.seq:
                self.changed.wait_for(lambda: self.seq != since, timeout=wait)
            return dict(self.state, seq=self.seq, now=time.time())

    def picture_bytes(self):
        with self.changed:
            picture, ready = self.picture, self.picture_jpeg
        if picture is None or ready is not None:
            return ready
        image = picture.copy()
        image.thumbnail((1920, 1080))
        out = io.BytesIO()
        image.convert("RGB").save(out, "JPEG", quality=85)
        with self.changed:
            if self.picture is picture:  # made once per poster, then every page gets the same bytes
                self.picture_jpeg = out.getvalue()
        return out.getvalue()

    # ---- the server ----
    def start(self):
        handler = self._handler()
        for port in (*PORTS, 0):  # 0: any free port, if all four are taken
            try:
                server = ExclusiveServer(("0.0.0.0", port), handler)
            except OSError:
                continue
            server.daemon_threads = True
            server.handle_error = _quiet_error
            self.server, self.port = server, server.server_address[1]
            threading.Thread(target=server.serve_forever, daemon=True).start()
            logging.info("Livestream and stage outputs ready on port %s", self.port)
            return True
        self.error = "Livestream and stage outputs unavailable: no free network port"
        logging.error(self.error)
        return False

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None

    def link(self, page, host="127.0.0.1", **extra):
        query = "".join(f"&{k}={v}" for k, v in extra.items())
        return f"http://{host}:{self.port}/{page}?k={self.token}{query}" if self.port else ""

    def network_links(self, page):
        return [self.link(page, address) for address in laptop_addresses()]

    def _handler(self):
        outputs = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                pass  # one line per page check would flood the log

            def _send(self, code, body=b"", kind="text/plain; charset=utf-8"):
                self.send_response(code)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                url = urlparse(self.path)
                query = parse_qs(url.query)
                if not secrets.compare_digest(query.get("k", [""])[0], outputs.token):
                    return self._send(403, b"Open the link from the Scripture Screen laptop again (STREAM & STAGE).")
                if url.path == "/live":
                    return self._send(200, LIVE_PAGE.encode("utf-8"), "text/html; charset=utf-8")
                if url.path == "/stage":
                    return self._send(200, STAGE_PAGE.encode("utf-8"), "text/html; charset=utf-8")
                if url.path == "/state":
                    try:
                        since = int(query["since"][0]) if "since" in query else None
                    except ValueError:
                        since = None
                    body = json.dumps(outputs.snapshot(since)).encode("utf-8")
                    return self._send(200, body, "application/json")
                if url.path == "/logo":
                    data = outputs.logo_png
                    return self._send(200, data, "image/png") if data else self._send(404)
                if url.path == "/picture":
                    data = outputs.picture_bytes()
                    return self._send(200, data, "image/jpeg") if data else self._send(404)
                self._send(404)

        return Handler


def _quiet_error(_request, _address):
    """OBS or a tablet closing the page mid-answer is normal, not a fault."""
    import sys

    if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
        return
    logging.exception("Livestream/stage page failed")


def new_token():
    return secrets.token_urlsafe(9)


# The pages ask /state?since=N; the laptop holds the answer until something changes, so a verse shows at once.
_POLL = r"""
const key = new URLSearchParams(location.search).get("k") || "";
let seq = null, failures = 0;
async function poll() {
  for (;;) {
    try {
      const r = await fetch("/state?k=" + encodeURIComponent(key) + (seq === null ? "" : "&since=" + seq),
                            { cache: "no-store" });
      if (!r.ok) throw new Error(r.status);
      const s = await r.json();
      failures = 0; offline(false);
      if (s.seq !== seq) { seq = s.seq; render(s); }
      tick(s);
    } catch (e) {
      failures++; if (failures > 1) offline(true);
      await new Promise(res => setTimeout(res, 1000));
    }
  }
}
function fit(el, max, min, room) {  // largest text size where the words fit the room
  let size = max; el.style.fontSize = size + "px";
  while (size > min && el.scrollHeight > room) { size -= 2; el.style.fontSize = size + "px"; }
}
"""

LIVE_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scripture Screen - Livestream</title>
<style>
  html, body { margin: 0; height: 100%; overflow: hidden; background: transparent; }
  body { font-family: Georgia, "Times New Roman", serif; color: #fff; }
  body.full { background: #000; }
  #box { position: absolute; opacity: 0; transition: opacity .35s ease; }
  #box.on { opacity: 1; }
  /* lower third: a dark band at the bottom, over the camera picture */
  body.lower #box { left: 5vw; right: 5vw; bottom: 5vh; padding: 1.6vh 2.4vw 2vh;
                    background: rgba(10, 12, 16, .82); border-left: .6vw solid #e6b85c; border-radius: .6vw; }
  body.lower #title { font-size: 3.4vh; }
  body.full #box { inset: 6vh 7vw; display: flex; flex-direction: column; justify-content: center; text-align: center; }
  body.full #title { font-size: 5.6vh; margin-bottom: 3vh; }
  #title { color: #e6b85c; font-weight: bold; margin-bottom: .8vh; }
  #words { line-height: 1.25; overflow: hidden; }
  #second { color: #ffe9b0; line-height: 1.25; margin-top: 1vh; overflow: hidden; }
  #picture { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; opacity: 0;
             transition: opacity .35s ease; }
  #picture.on { opacity: 1; }
  #logo { display: none; float: right; max-height: 7vh; max-width: 14vw; margin: 0 0 .6vh 1.4vw; opacity: .9; }
  body.full #logo { position: absolute; right: 0; bottom: -4vh; float: none; max-height: 8vh; opacity: .55; }
</style></head>
<body>
<img id="picture" alt="">
<div id="box"><img id="logo" alt=""><div id="title"></div><div id="words"></div><div id="second"></div></div>
<script>
const params = new URLSearchParams(location.search);
const full = params.get("style") === "full";
document.body.className = full ? "full" : "lower";
const box = document.getElementById("box"), title = document.getElementById("title"),
      words = document.getElementById("words"), second = document.getElementById("second"),
      picture = document.getElementById("picture");
function offline(_down) {}  // the livestream keeps the last screen rather than showing an error to viewers
function tick(_s) {}
const logo = document.getElementById("logo");
let logoVersion = -1;
function render(s) {
  const text = s.mode === "text";
  const accent = s.accent || "#e6b85c";
  title.style.color = accent;
  if (!full) box.style.borderLeftColor = accent;
  if (s.logo !== logoVersion) {
    logoVersion = s.logo;
    if (s.logo) { logo.src = "/logo?k=" + encodeURIComponent(key) + "&v=" + s.logo; logo.style.display = "block"; }
    else logo.style.display = "none";
  }
  box.classList.toggle("on", text);
  if (text) {
    title.textContent = s.title; words.textContent = s.body; second.textContent = s.second;
    second.style.display = s.second ? "" : "none";
    const h = innerHeight, room = full ? h * 0.7 : h * (s.second ? 0.16 : 0.2);
    if (s.second) { fit(words, h * (full ? .06 : .04), 12, room); fit(second, h * (full ? .06 : .04), 12, room); }
    else fit(words, h * (full ? .085 : .05), 12, room);
  }
  // pictures and posters show only on the full-screen output; the lower third never covers the camera with them
  const showPicture = full && s.mode === "picture";
  if (showPicture) picture.src = "/picture?k=" + encodeURIComponent(key) + "&v=" + s.seq;
  picture.classList.toggle("on", showPicture);
}
""" + _POLL + r"""
poll();
</script>
</body></html>
"""

STAGE_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scripture Screen - Stage</title>
<style>
  :root { color-scheme: dark; }
  html, body { margin: 0; height: 100%; background: #000; color: #f1f3f5; overflow: hidden;
               font-family: system-ui, "Segoe UI", sans-serif; }
  main { display: grid; grid-template-rows: auto minmax(0, 1fr) auto; height: 100vh; box-sizing: border-box; padding: 2vh 3vw; gap: 2vh; }
  header { display: flex; justify-content: space-between; align-items: center; }
  #clock { font-size: 7vh; font-weight: 700; font-variant-numeric: tabular-nums; }
  #label { font-size: 2.6vh; font-weight: 700; letter-spacing: .08em; color: #2f9e62; }
  #since { font-size: 2.6vh; color: #9aa4b1; font-variant-numeric: tabular-nums; }
  #now { overflow: hidden; display: flex; flex-direction: column; justify-content: center; min-height: 0; }
  #title { color: #e6b85c; font-weight: 700; font-size: 5vh; margin-bottom: 1.5vh; font-family: Georgia, serif; }
  #inner { flex-shrink: 0; }  /* its full height is measured, and the text shrinks until it fits #now */
  #words { font-family: Georgia, serif; line-height: 1.25; }
  #second { font-family: Georgia, serif; color: #ffe9b0; line-height: 1.25; margin-top: 1.5vh; }
  #next { border-top: .3vh solid #2a3038; padding-top: 1.5vh; font-size: 3vh; color: #9aa4b1;
          display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  #title { font-size: min(5vh, 7vw); }
  #clock { font-size: min(7vh, 12vw); }
  #next b { color: #4c8bf5; letter-spacing: .08em; margin-right: 1vw; }
  #note { display: none; background: #d6453d; color: #fff; font-size: 5vh; font-weight: 700; text-align: center;
          padding: 1.5vh 2vw; border-radius: 1vh; }
  #note.on { display: block; }
  #offline { display: none; color: #d6453d; font-size: 2.4vh; font-weight: 700; }
  #offline.on { display: inline; }
</style></head>
<body><main>
  <header>
    <div><div id="label">ON SCREEN</div><div id="since"></div><span id="offline">LOST THE LAPTOP, RETRYING</span></div>
    <div id="clock"></div>
  </header>
  <section id="now"><div id="inner"><div id="note"></div><div id="title"></div><div id="words"></div><div id="second"></div></div></section>
  <footer id="next"></footer>
</main>
<script>
const label = document.getElementById("label"), since = document.getElementById("since"),
      clock = document.getElementById("clock"), title = document.getElementById("title"),
      words = document.getElementById("words"), second = document.getElementById("second"),
      next = document.getElementById("next"), note = document.getElementById("note"),
      now = document.getElementById("now"), lost = document.getElementById("offline");
let shownAt = Date.now(), skew = 0;
const NAMES = { text: ["ON SCREEN", "#2f9e62"], picture: ["POSTER ON SCREEN", "#e6b85c"],
                video: ["VIDEO PLAYING", "#e6b85c"], black: ["SCREEN IS BLACK", "#d6453d"],
                standby: ["NOTHING ON SCREEN", "#9aa4b1"] };
function offline(down) { lost.classList.toggle("on", down); }
function two(n) { return String(n).padStart(2, "0"); }
function tick(s) { if (s) { skew = s.now * 1000 - Date.now(); shownAt = s.since * 1000 - skew; } }
function clockTick() {
  const d = new Date(); clock.textContent = two(d.getHours()) + ":" + two(d.getMinutes());
  const secs = Math.max(0, Math.floor((Date.now() - shownAt) / 1000));
  since.textContent = "for " + Math.floor(secs / 60) + ":" + two(secs % 60);
}
clockTick();  // now, so the header has its full height before the verse is measured
setInterval(clockTick, 500);
let hasSecond = false;
function refit() {  // both languages shrink together until the whole verse fits
  let size = Math.min(innerHeight * (hasSecond ? .07 : .1), innerWidth * .09);
  const set = () => { words.style.fontSize = second.style.fontSize = size + "px"; };
  set();
  const inner = document.getElementById("inner");
  while (size > 12 && inner.offsetHeight > now.clientHeight) { size -= 2; set(); }
}
addEventListener("resize", refit);
function render(s) {
  const [name, color] = NAMES[s.mode] || NAMES.standby;
  label.textContent = name; label.style.color = color;
  title.textContent = s.mode === "text" ? s.title : "";
  words.textContent = s.mode === "text" ? s.body : (s.mode === "standby" ? s.standby : "");
  second.textContent = s.mode === "text" ? s.second : "";
  second.style.display = second.textContent ? "" : "none";
  note.textContent = s.note; note.classList.toggle("on", !!s.note);
  next.innerHTML = "";
  if (s.next_title) {
    const tag = document.createElement("b"); tag.textContent = "NEXT";
    next.append(tag, s.next_title + (s.next_body ? "  " + s.next_body.slice(0, 140) + (s.next_body.length > 140 ? "..." : "") : ""));
  }
  hasSecond = !!second.textContent;
  refit();
}
""" + _POLL + r"""
poll();
</script>
</body></html>
"""
