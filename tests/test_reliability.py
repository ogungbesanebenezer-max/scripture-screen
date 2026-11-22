import threading
import time

import numpy as np

from verse_screen.reliability import acquire_single_instance
from verse_screen.speech import SAMPLE_RATE, Listener, RingBuffer, resample


class Flaky:
    """Fails on the first pass, then works: a crash must not end listening."""

    def __init__(self):
        self.calls = 0

    def transcribe(self, _audio):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("engine failed")
        return "hello"


def test_listener_survives_engine_failure():
    ring = RingBuffer()
    ring.add(np.full(SAMPLE_RATE * 2, 0.1, dtype=np.float32))
    got = threading.Event()
    listener = Listener(ring, Flaky(), lambda _text, _seconds: got.set(), step=0.1)
    listener.start()
    for _ in range(20):
        ring.add(np.full(SAMPLE_RATE // 2, 0.1, dtype=np.float32))
        time.sleep(0.1)
        if got.is_set():
            break
    listener.stop()
    assert got.is_set()


def test_ring_buffer_keeps_only_the_latest_audio():
    ring = RingBuffer()
    ring.add(np.zeros(SAMPLE_RATE * 30, dtype=np.float32))
    ring.add(np.ones(SAMPLE_RATE, dtype=np.float32))
    audio, _ = ring.latest(1.0)
    assert len(audio) == SAMPLE_RATE and audio.min() == 1.0


def test_resample_common_laptop_rates():
    for rate in (44100, 48000, 16000):
        assert abs(len(resample(np.zeros(rate, dtype=np.float32), rate)) - SAMPLE_RATE) <= 2


def test_only_one_copy_can_run():
    first = acquire_single_instance()
    try:
        assert first is not None and acquire_single_instance() is None
    finally:
        first.close()


def test_listener_reports_a_stall_and_a_dead_thread():
    import time as _time

    from verse_screen.speech import Listener, RingBuffer, speech_threads

    assert 1 <= speech_threads() <= 4
    import threading

    listener = Listener(RingBuffer(), transcriber=None, on_text=lambda *a: None)
    gate = threading.Event()
    listener.running, listener.thread = True, threading.Thread(target=gate.wait, daemon=True)
    listener.thread.start()
    assert not listener.stalled()
    listener.beat = _time.monotonic() - 60  # stuck inside one pass
    assert listener.stalled()
    listener.beat = _time.monotonic()
    gate.set()
    listener.thread.join()
    assert listener.stalled()  # thread died
    listener.stop()
    assert not listener.stalled()


def test_parakeet_that_cannot_load_falls_back_to_whisper(monkeypatch):
    """A missing or damaged Parakeet model must never leave the service without listening."""
    from verse_screen import speech

    made = []

    def broken(*_args, **_kwargs):
        raise OSError("model files missing")

    monkeypatch.setattr(speech, "ParakeetTranscriber", broken)
    monkeypatch.setattr(speech, "WhisperTranscriber", lambda name, *a, **k: made.append(name) or name)
    assert speech.Transcriber(speech.PARAKEET) == speech.FALLBACK_MODEL and made == ["base.en"]
    assert speech.Transcriber("tiny.en") == "tiny.en"  # other names go straight to Whisper
