"""Audio capture and speech recognition. Always processes the newest audio, so it never falls behind."""
import logging
import math
import os
import threading
import time
import wave

import numpy as np

from .bible import BOOKS, ROOT

SAMPLE_RATE = 16000
BUFFER_SECONDS = 20
MODEL_DIR = ROOT / "models"
# Book names only. A real reference here could be echoed back on noise and look like a detection, and a phrase
# such as "the book, chapter" was echoed in place of a misheard book name.
PROMPT = "A church sermon in Nigeria. Media, give me the Bible passage. Books: " + ", ".join(
    b for b in BOOKS if not b[0].isdigit()) + "."
# Whisper's own signals that a segment is noise or invented text.
MAX_NO_SPEECH = 0.6
MIN_CONFIDENCE = 0.5  # below this a phrase is mumbled or too far from the mic: ignore it and leave the screen alone
NO_SPEECH_LOGPROB = -0.5
MAX_COMPRESSION = 2.4
MAX_TOKENS = 90  # far more than 7 seconds of speech can produce


def speech_threads():
    """Leave one core for the screen, so the window never shows "Not responding" while the model works."""
    return max(1, min(4, (os.cpu_count() or 2) - 1))


def is_reliable(segment, min_confidence=MIN_CONFIDENCE):
    if segment.compression_ratio > MAX_COMPRESSION or math.exp(segment.avg_logprob) < min_confidence:
        return False
    # Whisper's own rule: silence only if it also has low confidence. A lone "Amen." often scores high on no_speech.
    return not (segment.no_speech_prob > MAX_NO_SPEECH and segment.avg_logprob < NO_SPEECH_LOGPROB)


class RingBuffer:
    def __init__(self):
        self.data = np.zeros(SAMPLE_RATE * BUFFER_SECONDS, dtype=np.float32)
        self.written = 0
        self.level = 0.0
        self.lock = threading.Lock()

    def add(self, samples):
        samples = samples[-len(self.data):]
        with self.lock:
            self.data = np.roll(self.data, -len(samples))
            self.data[-len(samples):] = samples
            self.written += len(samples)
            self.level = float(np.sqrt(np.mean(samples ** 2)))

    def latest(self, seconds):
        count = int(seconds * SAMPLE_RATE)
        with self.lock:
            return self.data[-min(count, self.written):].copy() if self.written else np.zeros(0, dtype=np.float32), self.written


def resample(samples, rate):
    """Average then interpolate down to 16 kHz; enough quality for speech."""
    if rate == SAMPLE_RATE:
        return samples
    factor = int(rate / SAMPLE_RATE)
    if factor > 1:
        usable = len(samples) - len(samples) % factor
        samples, rate = samples[:usable].reshape(-1, factor).mean(axis=1), rate / factor
    target = int(len(samples) * SAMPLE_RATE / rate)
    return np.interp(np.linspace(0, len(samples) - 1, target), np.arange(len(samples)), samples).astype(np.float32)


def list_microphones(refresh=False):
    """Return [(device_index, name)]. Empty if the audio system is unavailable.

    refresh re-reads the hardware, so a device plugged in after startup is found. No stream may be open.
    """
    try:
        import sounddevice as sd

        if refresh:
            sd._terminate()
            sd._initialize()
        api = sd.default.hostapi
        return [(i, d["name"]) for i, d in enumerate(sd.query_devices())
                if d["max_input_channels"] > 0 and d["hostapi"] == api]
    except Exception:
        return []


class Microphone:
    def __init__(self, ring, device=None, muted=None):
        import sounddevice as sd

        self.muted = muted  # while the phone mic is sending, the laptop mic stays out of the way

        info = sd.query_devices(device, "input")
        self.rate = int(info["default_samplerate"])
        self.ring = ring
        self.last_audio = time.monotonic()
        # Mixer feeds are often live on one side only, so use the louder channel.
        self.stream = sd.InputStream(device=device, channels=min(2, info["max_input_channels"]), samplerate=self.rate,
                                     dtype="float32", blocksize=int(self.rate / 5), callback=self._on_audio)
        self.stream.start()

    def _on_audio(self, block, _frames, _time, _status):
        self.last_audio = time.monotonic()
        if self.muted and self.muted():
            return
        channel = block[:, int(np.argmax(np.abs(block).mean(axis=0)))]
        self.ring.add(resample(channel.copy(), self.rate))

    def alive(self):
        """False once the device has been unplugged or has stopped delivering audio."""
        return self.stream.active and time.monotonic() - self.last_audio < 3.0

    def stop(self):
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:  # an unplugged device can fail on close
            logging.exception("Could not close the audio input cleanly")


class FilePlayer:
    """Feeds a 16-bit WAV in real time, standing in for a microphone during tests and rehearsals."""

    def __init__(self, ring, path, speed=1.0, on_end=None):
        with wave.open(str(path), "rb") as source:
            rate, channels = source.getframerate(), source.getnchannels()
            audio = np.frombuffer(source.readframes(source.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        self.audio = resample(audio[::channels], rate)
        self.ring, self.speed, self.on_end = ring, speed, on_end
        self.running = True
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        step = SAMPLE_RATE // 5
        for start in range(0, len(self.audio), step):
            if not self.running:
                return
            self.ring.add(self.audio[start:start + step])
            time.sleep(0.2 / self.speed)
        if self.on_end:
            self.on_end()

    def alive(self):
        return True

    def stop(self):
        self.running = False


PARAKEET = "parakeet"  # settings name; NVIDIA Parakeet TDT 0.6B v2, the model the Handy dictation app recommends
PARAKEET_SOURCE = "nemo-parakeet-tdt-0.6b-v2"
PARAKEET_DIR = MODEL_DIR / "parakeet-tdt-0.6b-v2"
FALLBACK_MODEL = "base.en"


def Transcriber(model_name, threads=0, min_confidence=MIN_CONFIDENCE):
    """Speech engine for a settings name. If Parakeet cannot load, listen with Whisper rather than not at all."""
    if model_name != PARAKEET:
        return WhisperTranscriber(model_name, threads, min_confidence)
    try:
        return ParakeetTranscriber(threads, min_confidence)
    except Exception:
        logging.exception("Parakeet could not load; using Whisper %s", FALLBACK_MODEL)
        return WhisperTranscriber(FALLBACK_MODEL, threads, min_confidence)


def speech_only(audio):
    """Cut out the silence and music between phrases (the same Silero detector Whisper's vad_filter uses)."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    spans = get_speech_timestamps(audio, VadOptions(min_silence_duration_ms=400))
    return np.concatenate([audio[s["start"]:s["end"]] for s in spans]) if spans else audio[:0]


class ParakeetTranscriber:
    """Same job as WhisperTranscriber. Accent bench 2026-11-13: 98% right in 0.4 s against base.en 94% in 1.0 s,
    and it writes nothing at all for silence, hum, organ chords or clapping."""

    name = PARAKEET

    def __init__(self, threads=0, min_confidence=MIN_CONFIDENCE):
        import onnx_asr
        import onnxruntime

        self.min_confidence = min_confidence
        self.hearing = None
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = threads or speech_threads()
        options.inter_op_num_threads = 1
        options.log_severity_level = 3  # its start-up warnings are harmless and would fill the log
        # Loads from models/ when the files are there; downloads them there otherwise (setup, with internet).
        self.model = onnx_asr.load_model(PARAKEET_SOURCE, PARAKEET_DIR, quantization="int8",
                                         sess_options=options).with_timestamps()

    def transcribe(self, audio, vad=True, beam_size=1):
        if vad:
            audio = speech_only(audio)
        if len(audio) < SAMPLE_RATE // 4:
            self.hearing = None
            return ""
        result = self.model.recognize(audio.astype(np.float32), sample_rate=SAMPLE_RATE)
        logprobs = result.logprobs or []
        self.hearing = math.exp(sum(logprobs) / len(logprobs)) if logprobs else None
        if self.hearing is None or self.hearing < self.min_confidence:
            return ""
        return result.text.strip()


class WhisperTranscriber:
    def __init__(self, model_name, threads=0, min_confidence=MIN_CONFIDENCE):
        from faster_whisper import WhisperModel

        self.name = model_name

        self.min_confidence = min_confidence
        self.hearing = None  # 0-1, how sure the model was of the weakest phrase in the last pass
        options = dict(device="cpu", compute_type="int8", cpu_threads=threads or speech_threads(),
                       download_root=str(MODEL_DIR))
        try:
            self.model = WhisperModel(model_name, local_files_only=True, **options)
        except Exception:  # not cached yet; only possible with internet
            self.model = WhisperModel(model_name, **options)

    def transcribe(self, audio, vad=True, beam_size=1):
        segments, _ = self.model.transcribe(
            audio, language="en", beam_size=beam_size, vad_filter=vad, vad_parameters={"min_silence_duration_ms": 400},
            initial_prompt=PROMPT, condition_on_previous_text=False, without_timestamps=True,
            temperature=0.0,  # no retries: they stall for many seconds and are where invented text comes from
            max_new_tokens=MAX_TOKENS)
        heard = list(segments)
        self.hearing = min((math.exp(segment.avg_logprob) for segment in heard), default=None)
        return " ".join(s.text.strip() for s in heard if is_reliable(s, self.min_confidence)).strip()


class Listener:
    """Background loop: every step, transcribe the last few seconds and hand the text on."""

    def __init__(self, ring, transcriber, on_text, window=7.0, step=1.5, silence=0.003):
        self.ring, self.transcriber, self.on_text = ring, transcriber, on_text
        self.window, self.step, self.silence = window, step, silence
        self.running = False
        self.last_seconds = 0.0
        self.beat = time.monotonic()  # updated every loop; a stale beat means listening has stalled
        self.thread = None

    def start(self):
        self.running = True
        self.beat = time.monotonic()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stalled(self, limit=30.0):
        return self.running and (not self.thread.is_alive() or time.monotonic() - self.beat > limit)

    def stop(self):
        self.running = False

    def _run(self):
        done = 0
        while self.running:
            self.beat = time.monotonic()
            audio, written = self.ring.latest(self.window)
            if written - done < SAMPLE_RATE * self.step or len(audio) < SAMPLE_RATE:
                time.sleep(0.05)
                continue
            done = written
            if np.sqrt(np.mean(audio ** 2)) < self.silence:
                continue
            try:
                began = time.perf_counter()
                text = self.transcriber.transcribe(audio)
                self.last_seconds = time.perf_counter() - began
                if text:
                    self.on_text(text, self.last_seconds)
            except Exception:  # one bad pass must never end listening for the rest of the service
                logging.exception("Speech pass failed")
                time.sleep(0.5)
