"""Sermon test (owner, 2026-11-18): play a recorded sermon through the app as if it came from the mixer, watch the
verses go up, and log every sentence with the sermon time so scripts/sermon_check.py can compare it with the
video's own transcript and show where the app is late, misses a verse or puts up a wrong one.

The sermon plays on the speakers through the built-in player; the speech engine gets the same sound straight from
the file, kept in step with the player, so pause, skip and seek stay in sync and the microphone is not involved.
"""
import logging
import threading
import time

import numpy as np

SAMPLE_RATE = 16000
FEED_EVERY = 0.2
JUMP = 3.0  # a position change bigger than this is a seek: start listening from the new place


class SermonAudio:
    """The sermon's sound at 16 kHz mono, decoded on a worker thread (about 80 times faster than it plays)."""

    def __init__(self, source, headers=None):
        self.source, self.headers = str(source), headers or {}
        self.data = bytearray()  # 16-bit samples
        self.lock = threading.Lock()
        self.done, self.error, self.running = False, None, True
        threading.Thread(target=self._decode, daemon=True).start()

    @property
    def seconds(self):
        return len(self.data) / 2 / SAMPLE_RATE

    def _decode(self):
        try:
            import av

            options = {}
            if self.headers:
                options["headers"] = "".join(f"{k}: {v}\r\n" for k, v in self.headers.items())
            with av.open(self.source, options=options, timeout=30) as box:
                stream = box.streams.audio[0]
                resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
                for frame in box.decode(stream):
                    if not self.running:
                        return
                    for out in resampler.resample(frame):
                        self._add(out)
                for out in resampler.resample(None):
                    self._add(out)
        except Exception as error:
            logging.exception("Sermon audio could not be read")
            self.error = str(error)
        finally:
            self.done = True

    def _add(self, frame):
        chunk = frame.to_ndarray().reshape(-1).astype(np.int16).tobytes()
        with self.lock:
            self.data += chunk

    def samples(self, start, end):
        """float32 samples from start to end (sample numbers), as far as decoded."""
        with self.lock:
            raw = bytes(self.data[start * 2:end * 2])
        return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768

    def stop(self):
        self.running = False


class SermonFeeder:
    """Stands in for the microphone: hands the speech engine whatever the player has just played."""

    def __init__(self, ring, audio, player):
        self.ring, self.audio, self.player = ring, audio, player
        self.fed = 0  # samples given to the speech engine so far (its position in the sermon)
        self.running = True
        threading.Thread(target=self._run, daemon=True).start()

    @property
    def position(self):
        return self.fed / SAMPLE_RATE

    def _run(self):
        while self.running:
            time.sleep(FEED_EVERY)
            try:
                position = self.player.position
                if position is None or self.player.paused:
                    continue
                target = int(position * SAMPLE_RATE)
                if abs(target - self.fed) > JUMP * SAMPLE_RATE:
                    self.fed = target  # the operator skipped: listen from the new place
                    continue
                end = min(target, len(self.audio.data) // 2)
                if end > self.fed:
                    self.ring.add(self.audio.samples(self.fed, end))
                    self.fed = end
            except Exception:
                logging.exception("Sermon test feed failed")

    def alive(self):
        return self.running

    def stop(self):
        self.running = False
