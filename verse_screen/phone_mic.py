"""Phone as microphone: the laptop serves a page, the phone opens it by QR code and streams the pastor's voice.

No app on the phone and no internet. Phone and laptop only need to share a network: the phone's own hotspot (the
laptop joins it) or the laptop's Windows hotspot (the phone joins it). Phones allow the microphone only on https,
so the laptop makes its own certificate once; the phone shows a warning the first time, which is expected.
"""
import datetime
import io
import ipaddress
import logging
import secrets
import socket
import ssl
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import numpy as np

from .bible import ROOT
from .speech import resample

PORTS = (8765, 8766, 8767, 8768)
CERT_DIR = ROOT / "data" / "phone_mic"  # private to this laptop; never in git
ACTIVE_SECONDS = 2.5  # no sound from the phone for this long: the laptop mic takes over again
MAX_CHUNK_BYTES = 2_000_000


class ExclusiveServer(ThreadingHTTPServer):
    """On Windows the usual "reuse address" lets two programs listen on one port at once, and the second silently
    gets nothing. There the port is claimed for this program alone, so a taken port moves on to the next one."""

    allow_reuse_address = sys.platform != "win32"

    def server_bind(self):
        if sys.platform == "win32":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def laptop_addresses():
    """IPv4 addresses the phone could reach, best first: hotspot networks, then home/church Wi-Fi."""
    found = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))  # sends nothing; only asks which address would be used
        found.add(probe.getsockname()[0])
    except OSError:
        pass
    finally:
        probe.close()
    usable = [a for a in found if not ipaddress.ip_address(a).is_loopback and not a.startswith("169.254.")]

    def rank(address):
        if address.startswith("192.168.137."):
            return 0  # the Windows "Mobile hotspot"
        if address.startswith(("192.168.43.", "172.20.10.", "192.168.49.")):
            return 1  # joined to an Android or iPhone hotspot
        if ipaddress.ip_address(address) in ipaddress.ip_network("172.16.0.0/12"):
            return 3  # usually the laptop's own virtual adapters (WSL, Docker, VPN), not a network a phone is on
        return 2 if ipaddress.ip_address(address).is_private else 4
    return sorted(usable, key=lambda a: (rank(a), a))


def ensure_certificate(addresses):
    """A self-signed certificate made on this laptop the first time; reused after that."""
    cert, key = CERT_DIR / "cert.pem", CERT_DIR / "key.pem"
    if cert.exists() and key.exists():
        return cert, key
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    CERT_DIR.mkdir(parents=True, exist_ok=True)
    private = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Scripture Screen")])
    names = [x509.DNSName("localhost")] + [x509.IPAddress(ipaddress.ip_address(a)) for a in addresses]
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(private.public_key())
                   .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
                   .not_valid_after(now + datetime.timedelta(days=3650))
                   .add_extension(x509.SubjectAlternativeName(names), critical=False)
                   .sign(private, hashes.SHA256()))
    key.write_bytes(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    return cert, key


def qr_png(text, scale=8):
    import segno

    out = io.BytesIO()
    segno.make(text, error="m").save(out, kind="png", scale=scale, border=3, dark="#000000", light="#ffffff")
    return out.getvalue()


class PhoneMic:
    """Receives the phone's audio and writes it into the same buffer the laptop microphone uses."""

    def __init__(self, ring, token):
        self.ring, self.token = ring, token
        self.last_audio = 0.0
        self.phone_name = ""
        self.port = None
        self.server = None
        self.error = None

    def start(self):
        addresses = laptop_addresses()
        try:
            cert, key = ensure_certificate(addresses)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert, key)
        except Exception as error:
            self.error = f"Phone mic unavailable: {error}"
            logging.exception("Phone mic certificate failed")
            return False
        handler = self._handler()
        for port in PORTS:
            try:
                server = ExclusiveServer(("0.0.0.0", port), handler)
            except OSError:
                continue
            server.handle_error = _quiet_error
            server.socket = context.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
            server.daemon_threads = True
            self.server, self.port = server, port
            threading.Thread(target=server.serve_forever, daemon=True).start()
            logging.info("Phone mic ready on port %s (%s)", port, ", ".join(addresses) or "no network yet")
            return True
        self.error = "Phone mic unavailable: no free network port"
        logging.error(self.error)
        return False

    def urls(self):
        return [f"https://{a}:{self.port}/?k={self.token}" for a in laptop_addresses()] if self.port else []

    def active(self):
        return time.monotonic() - self.last_audio < ACTIVE_SECONDS

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None

    def receive(self, body, rate):
        samples = np.frombuffer(body[:len(body) - len(body) % 2], dtype="<i2").astype(np.float32) / 32768
        if len(samples):
            self.ring.add(resample(samples, rate))
            self.last_audio = time.monotonic()

    def _handler(self):
        mic = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                pass  # one line per audio chunk would flood the log

            def _allowed(self):
                return secrets.compare_digest(parse_qs(urlparse(self.path).query).get("k", [""])[0], mic.token)

            def _send(self, code, body=b"", kind="text/plain"):
                self.send_response(code)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if urlparse(self.path).path != "/" or not self._allowed():
                    return self._send(403, b"Scan the QR code on the Scripture Screen laptop again.")
                self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                if urlparse(self.path).path != "/audio" or not self._allowed() or not 0 < length <= MAX_CHUNK_BYTES:
                    self.rfile.read(min(length, MAX_CHUNK_BYTES))
                    return self._send(403)
                body = self.rfile.read(length)
                query = parse_qs(urlparse(self.path).query)
                try:
                    rate = int(query.get("rate", ["16000"])[0])
                    if not 8000 <= rate <= 96000:
                        raise ValueError(rate)
                    mic.phone_name = self.headers.get("User-Agent", "")[:120]
                    mic.receive(body, rate)
                except Exception:
                    logging.exception("Bad phone audio chunk")
                    return self._send(400)
                self._send(204)

        return Handler


def _quiet_error(_request, _address):
    """A phone that has not yet accepted the laptop's certificate drops the connection; that is expected, not a fault."""
    import sys

    error = sys.exc_info()[1]
    if isinstance(error, (ssl.SSLError, ConnectionError, TimeoutError)):
        return
    logging.exception("Phone mic connection failed")


def new_token():
    return secrets.token_urlsafe(9)


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scripture Screen Mic</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; background: #14171c; color: #f1f3f5; font: 18px/1.4 system-ui, sans-serif;
         display: flex; min-height: 100vh; align-items: center; justify-content: center; text-align: center; }
  main { width: 100%; max-width: 440px; padding: 24px 16px; }
  h1 { font-size: 22px; color: #e6b85c; margin: 0 0 18px; }
  #state { font-size: 26px; font-weight: 700; margin: 18px 0; min-height: 70px; }
  .ok { color: #2f9e62; } .bad { color: #d6453d; } .wait { color: #9aa4b1; }
  button { width: 100%; font-size: 24px; font-weight: 700; padding: 22px; border: 0; border-radius: 14px;
           color: #fff; background: #2f9e62; }
  button.stop { background: #d6453d; }
  #meter { height: 18px; background: #2a3038; border-radius: 9px; overflow: hidden; margin: 18px 0; }
  #bar { height: 100%; width: 0; background: #2f9e62; transition: width .1s; }
  p { color: #9aa4b1; font-size: 16px; }
</style></head>
<body><main>
  <h1>Scripture Screen Mic</h1>
  <button id="go">START</button>
  <div id="state" class="wait">Tap START, then allow the microphone.</div>
  <div id="meter"><div id="bar"></div></div>
  <p>Put this phone on the pulpit, near the pastor.<br>Keep this page open and the screen on.</p>
</main>
<script>
const key = new URLSearchParams(location.search).get("k") || "";
const go = document.getElementById("go"), state = document.getElementById("state"), bar = document.getElementById("bar");
const TARGET = 16000;
let ctx = null, stream = null, node = null, lock = null, queue = [], sending = false, running = false, lastOk = 0;

function show(text, kind) { state.textContent = text; state.className = kind; }

function downsample(input, rate) {
  if (rate <= TARGET) return { data: input, rate };
  const ratio = rate / TARGET, out = new Float32Array(Math.floor(input.length / ratio));
  for (let i = 0; i < out.length; i++) {
    const a = Math.floor(i * ratio), b = Math.min(input.length, Math.floor((i + 1) * ratio));
    let sum = 0; for (let j = a; j < b; j++) sum += input[j];
    out[i] = sum / Math.max(1, b - a);
  }
  return { data: out, rate: TARGET };
}

async function pump() {
  if (sending) return;
  sending = true;
  while (running && queue.length) {
    while (queue.length > 12) queue.shift();  // network was slow: drop old sound, stay live
    const chunk = queue.shift();
    try {
      const r = await fetch("/audio?k=" + encodeURIComponent(key) + "&rate=" + chunk.rate,
                            { method: "POST", body: chunk.pcm, headers: { "Content-Type": "application/octet-stream" } });
      if (r.ok) { lastOk = Date.now(); show("Connected. Listening.", "ok"); }
      else if (r.status === 403) { show("Scan the QR code on the laptop again.", "bad"); }
    } catch (e) {
      show("Lost the laptop. Retrying...", "bad");
      await new Promise(res => setTimeout(res, 800));
    }
  }
  sending = false;
}

async function keepAwake() {
  try { if ("wakeLock" in navigator) lock = await navigator.wakeLock.request("screen"); } catch (e) {}
}

async function start() {
  if (!window.isSecureContext || !navigator.mediaDevices) {
    show("This page must be opened with https from the QR code.", "bad"); return;
  }
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: {
      echoCancellation: false, noiseSuppression: true, autoGainControl: true, channelCount: 1 } });
  } catch (e) { show("Microphone not allowed. Allow it in the browser settings, then tap START.", "bad"); return; }
  ctx = new (window.AudioContext || window.webkitAudioContext)();
  await ctx.resume();
  const source = ctx.createMediaStreamSource(stream);
  node = ctx.createScriptProcessor(4096, 1, 1);
  let pending = [], pendingLen = 0;
  node.onaudioprocess = (event) => {
    if (!running) return;
    const input = event.inputBuffer.getChannelData(0);
    let peak = 0; for (let i = 0; i < input.length; i += 16) peak = Math.max(peak, Math.abs(input[i]));
    bar.style.width = Math.min(100, peak * 140) + "%";
    const d = downsample(input, ctx.sampleRate);
    pending.push(d.data); pendingLen += d.data.length;
    if (pendingLen >= d.rate / 4) {  // send about 4 times a second
      const pcm = new Int16Array(pendingLen); let at = 0;
      for (const part of pending) for (let i = 0; i < part.length; i++) {
        const s = Math.max(-1, Math.min(1, part[i])); pcm[at++] = s < 0 ? s * 32768 : s * 32767;
      }
      pending = []; pendingLen = 0;
      queue.push({ pcm: pcm.buffer, rate: d.rate }); pump();
    }
  };
  source.connect(node); node.connect(ctx.destination);
  running = true; keepAwake();
  go.textContent = "STOP"; go.className = "stop";
  show("Connecting to the laptop...", "wait");
}

function stop() {
  running = false; queue = [];
  if (node) node.disconnect();
  if (stream) stream.getTracks().forEach(t => t.stop());
  if (ctx) ctx.close();
  if (lock) lock.release().catch(() => {});
  node = stream = ctx = lock = null;
  bar.style.width = "0";
  go.textContent = "START"; go.className = "";
  show("Stopped. The laptop uses its own mic now.", "wait");
}

go.onclick = () => running ? stop() : start();
document.addEventListener("visibilitychange", () => { if (running && document.visibilityState === "visible") keepAwake(); });
setInterval(() => { if (running && lastOk && Date.now() - lastOk > 3000) show("Lost the laptop. Retrying...", "bad"); }, 1000);
</script>
</body></html>
"""
