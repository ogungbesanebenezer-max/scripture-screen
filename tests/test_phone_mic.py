import ssl
import urllib.error
import urllib.request

import numpy as np
import pytest

from verse_screen import phone_mic
from verse_screen.speech import RingBuffer


@pytest.fixture
def mic(tmp_path, monkeypatch):
    monkeypatch.setattr(phone_mic, "CERT_DIR", tmp_path / "cert")
    server = phone_mic.PhoneMic(RingBuffer(), "secret-key")
    assert server.start()
    yield server
    server.stop()


def call(mic, path, body=None):
    context = ssl.create_default_context()
    context.check_hostname, context.verify_mode = False, ssl.CERT_NONE  # the phone accepts the warning once
    request = urllib.request.Request(f"https://127.0.0.1:{mic.port}{path}", data=body, method="POST" if body else "GET")
    return urllib.request.urlopen(request, context=context, timeout=5)


def test_page_opens_with_the_key_from_the_qr(mic):
    page = call(mic, "/?k=secret-key").read().decode()
    assert "START" in page and "getUserMedia" in page


def test_wrong_key_is_refused(mic):
    with pytest.raises(urllib.error.HTTPError) as refused:
        call(mic, "/?k=guess")
    assert refused.value.code == 403
    with pytest.raises(urllib.error.HTTPError):
        call(mic, "/audio?k=guess&rate=16000", b"\x00\x10" * 4000)
    assert not mic.active()


def test_phone_audio_reaches_the_listener(mic):
    tone = (np.sin(np.arange(48000) / 48000 * 2 * np.pi * 300) * 12000).astype("<i2")  # 1 s at 48 kHz
    assert call(mic, "/audio?k=secret-key&rate=48000", tone.tobytes()).status == 204
    assert mic.active()
    audio, written = mic.ring.latest(1.0)
    assert written == 16000 and audio.max() > 0.3


def test_laptop_mic_steps_aside_while_the_phone_sends(mic):
    class Fake:
        muted = staticmethod(mic.active)
    assert Fake.muted() is False
    call(mic, "/audio?k=secret-key&rate=16000", np.full(4000, 3000, dtype="<i2").tobytes())
    assert Fake.muted() is True


def test_qr_is_a_png():
    assert phone_mic.qr_png("https://192.168.137.1:8765/?k=x").startswith(b"\x89PNG")


class _NoProbe:
    """laptop_addresses also asks the real network which address it would use; that must not leak in (on
    2026-11-17 the laptop was on an iPhone hotspot, 172.20.10.x, and that real address won over the fake ones)."""

    def __init__(self, *_a):
        pass

    def connect(self, _address):
        raise OSError("no network in this test")

    def close(self):
        pass


@pytest.fixture
def no_real_network(monkeypatch):
    monkeypatch.setattr(phone_mic.socket, "socket", _NoProbe)


def test_hotspot_address_comes_first(monkeypatch, no_real_network):
    monkeypatch.setattr(phone_mic.socket, "getaddrinfo", lambda *_a: [(0, 0, 0, "", (a, 0)) for a in
                        ("10.0.0.5", "192.168.137.1", "127.0.0.1", "169.254.3.3")])
    found = phone_mic.laptop_addresses()
    assert found[0] == "192.168.137.1" and "127.0.0.1" not in found and "169.254.3.3" not in found


def test_phone_hotspot_and_wifi_come_before_virtual_adapters(monkeypatch, no_real_network):
    monkeypatch.setattr(phone_mic.socket, "getaddrinfo", lambda *_a: [(0, 0, 0, "", (a, 0)) for a in
                        ("172.17.144.1", "192.168.1.20")])
    assert phone_mic.laptop_addresses()[0] == "192.168.1.20"
    monkeypatch.setattr(phone_mic.socket, "getaddrinfo", lambda *_a: [(0, 0, 0, "", (a, 0)) for a in
                        ("172.17.144.1", "172.20.10.2")])
    assert phone_mic.laptop_addresses()[0] == "172.20.10.2"


def test_phone_refusing_the_certificate_prints_nothing(capsys):
    import ssl
    try:
        raise ssl.SSLError("certificate unknown")
    except ssl.SSLError:
        phone_mic._quiet_error(None, None)
    assert capsys.readouterr().err == ""
