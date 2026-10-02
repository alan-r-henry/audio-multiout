"""Fans one endpoint's audio out to several others, in our own process.

One device is the *primary*: it stays the Windows default and plays natively,
so nothing is added to its path. The rest are *mirrors*, fed from a WASAPI
loopback capture of the primary.

The two ends run on independent hardware clocks, so a mirror slowly gains or
loses frames against the primary -- a few parts per million, but enough to
overflow or empty a buffer over minutes. Every service pass measures how much
audio is in flight for each mirror and nudges it back toward target by at most
a millisecond, which is far faster than the drift and small enough to be
inaudible.
"""

import threading
import time

import comtypes
import numpy as np

import wasapi
import winaudio

# How much audio to keep queued ahead on each mirror. Lower is tighter sync,
# higher survives scheduling hiccups. 20ms is about the floor for Python.
DEFAULT_TARGET_FILL_MS = 20

# Capture arrives in ~10ms packets, so the instantaneous queue depth swings by
# that much on its own. Correcting against the raw number would fire on every
# pass and eat real audio, so the depth is smoothed and corrections are both
# rate-limited and tiny. Hardware drift is a few ppm -- 1ms twice a second is
# already orders of magnitude more headroom than that needs.
DRIFT_TOLERANCE_MS = 10
MAX_CORRECTION_MS = 1
CORRECTION_INTERVAL = 0.5
DEPTH_SMOOTHING = 0.05

SERVICE_INTERVAL = 0.004

# Ceiling on digital boost. Windows' own volume APIs -- endpoint and session
# alike -- top out at 100% by construction, so going past that means scaling
# sample values ourselves rather than asking the OS for more. 3x (~+9.5dB) is
# already well past where most sources start clipping audibly; there is
# nothing to gain (pun intended) from allowing more.
MAX_GAIN = 3.0

# Boost is applied on whichever of these the mix format turns out to be.
# Shared-mode WASAPI on modern Windows is float32 in the overwhelming
# majority of cases; 16-bit PCM is the other format worth handling. Anything
# else is left untouched rather than risk corrupting the stream.
_DTYPE_BY_BITS = {32: np.dtype("<f4"), 16: np.dtype("<i2")}


def _apply_gain(data, gain, bits_per_sample):
    """Scale raw PCM/float bytes by `gain`, hard-clipped to full scale.

    Hard clipping distorts anything that was already near full scale --
    that is an inherent cost of boosting past unity digitally, not a bug.
    """
    if gain == 1.0 or not data:
        return data
    dtype = _DTYPE_BY_BITS.get(bits_per_sample)
    if dtype is None:
        return data
    samples = np.frombuffer(data, dtype=dtype)
    if dtype.kind == "f":
        boosted = np.clip(samples.astype(np.float32) * gain, -1.0, 1.0)
        return boosted.astype(dtype).tobytes()
    peak = np.iinfo(dtype).max
    boosted = np.clip(samples.astype(np.float32) * gain, -peak - 1, peak)
    return boosted.astype(dtype).tobytes()


class Mirror:
    """One endpoint being fed a copy of the primary's audio."""

    def __init__(self, endpoint, renderer, sample_rate, frame_bytes,
                 bits_per_sample, delay_ms=0, gain=1.0,
                 target_fill_ms=DEFAULT_TARGET_FILL_MS):
        self.endpoint = endpoint
        self.renderer = renderer
        self.sample_rate = sample_rate
        self.frame_bytes = frame_bytes
        self.bits_per_sample = bits_per_sample
        self.queue = bytearray()
        self.delay_ms = delay_ms
        self.gain = gain
        self.target_fill_ms = target_fill_ms
        self.failed = None
        self.corrections = 0
        self.underruns = 0
        self.dropped_frames = 0
        self.inserted_frames = 0
        self._smoothed_depth = None
        self._last_correction = 0.0

    def _ms_to_frames(self, ms):
        return int(self.sample_rate * ms / 1000)

    @property
    def target_frames(self):
        return self._ms_to_frames(self.target_fill_ms + self.delay_ms)

    def offset_ms(self):
        """How far behind the primary this mirror currently is."""
        try:
            padding = self.renderer.padding_frames()
        except (comtypes.COMError, OSError):
            return None
        queued = len(self.queue) // self.frame_bytes
        return (padding + queued) * 1000.0 / self.sample_rate

    def prime(self):
        """Pre-fill with silence so playback starts at the target depth.

        Without this the stream begins empty and underruns once while it fills,
        and the configured delay would not take effect until drift correction
        happened to walk it there.
        """
        try:
            written = self.renderer.write_silence(self.target_frames)
            self._smoothed_depth = float(written // self.frame_bytes)
        except Exception as exc:
            self.failed = f"{self.endpoint.name}: could not prime ({exc!r})"

    def feed(self, data):
        if data and self.failed is None:
            self.queue += _apply_gain(data, self.gain, self.bits_per_sample)

    def service(self):
        """Push queued audio out and correct drift. Returns False once dead."""
        if self.failed is not None:
            return False
        try:
            padding = self.renderer.padding_frames()
            queued = len(self.queue) // self.frame_bytes
            in_flight = padding + queued

            if self._smoothed_depth is None:
                self._smoothed_depth = float(in_flight)
            else:
                self._smoothed_depth += DEPTH_SMOOTHING * (
                    in_flight - self._smoothed_depth)

            now = time.monotonic()
            if now - self._last_correction >= CORRECTION_INTERVAL:
                error = int(self._smoothed_depth) - self.target_frames
                step = self._ms_to_frames(MAX_CORRECTION_MS)
                tolerance = self._ms_to_frames(DRIFT_TOLERANCE_MS)

                if error > tolerance and queued:
                    # Running ahead: discard the oldest frames.
                    drop = min(error, step, queued)
                    del self.queue[:drop * self.frame_bytes]
                    self.dropped_frames += drop
                    self.corrections += 1
                    self._last_correction = now
                elif error < -tolerance:
                    # Running behind: pad with silence so we do not underrun.
                    inserted = self.renderer.write_silence(min(-error, step))
                    self.inserted_frames += inserted // self.frame_bytes
                    self.corrections += 1
                    self._last_correction = now

            if self.queue:
                written = self.renderer.write(memoryview(self.queue))
                if written:
                    del self.queue[:written]
            elif padding == 0:
                # Nothing queued and the device has run dry.
                self.renderer.write_silence(self._ms_to_frames(5))
                self.underruns += 1
            return True
        except Exception as exc:
            # Anything at all here means this mirror is done. Catching broadly
            # keeps one bad endpoint (or one bug) from taking the others down
            # with it, and records why rather than dying silently.
            self.failed = f"{self.endpoint.name}: stream lost ({exc!r})"
            return False

    def close(self):
        try:
            self.renderer.stop()
        except Exception:
            pass


class Router:
    """Owns the capture thread and the mirror set."""

    def __init__(self):
        self.thread = None
        self.running = threading.Event()
        self.lock = threading.Lock()
        self.capture = None
        self.mirrors = []
        self.primary = None
        self.format = None
        self.error = None
        self.started_at = None

    # -- state -------------------------------------------------------------

    @property
    def active(self):
        return self.thread is not None and self.thread.is_alive()

    def describe_format(self):
        if self.format is None:
            return "--"
        return self.format.describe()

    def stats(self):
        with self.lock:
            return [{
                "name": m.endpoint.name,
                "id": m.endpoint.id,
                "offset_ms": m.offset_ms(),
                "delay_ms": m.delay_ms,
                "gain": m.gain,
                "corrections": m.corrections,
                "underruns": m.underruns,
                "dropped_frames": m.dropped_frames,
                "inserted_frames": m.inserted_frames,
                "failed": m.failed,
            } for m in self.mirrors]

    def set_delay(self, endpoint_id, delay_ms):
        with self.lock:
            for mirror in self.mirrors:
                if mirror.endpoint.id == endpoint_id:
                    mirror.delay_ms = max(0, min(500, int(delay_ms)))
                    return True
        return False

    def set_gain(self, endpoint_id, gain):
        with self.lock:
            for mirror in self.mirrors:
                if mirror.endpoint.id == endpoint_id:
                    mirror.gain = max(0.0, min(MAX_GAIN, float(gain)))
                    return True
        return False

    # -- lifecycle ---------------------------------------------------------

    def start(self, primary, mirrors, delays=None, gains=None, target_fill_ms=None):
        """Begin mirroring `primary` onto `mirrors`. Returns (ok, message)."""
        self.stop()
        self.error = None
        delays = delays or {}
        gains = gains or {}
        fill = target_fill_ms or DEFAULT_TARGET_FILL_MS

        try:
            comtypes.CoInitialize()
            enumerator = winaudio._enumerator()
            source_device = enumerator.GetDevice(primary.id)
            capture = wasapi.LoopbackCapture(source_device)
            fmt = capture.open()
        except (comtypes.COMError, OSError, StopIteration) as exc:
            self.error = f"could not open loopback on {primary.name}: {exc}"
            return False, self.error

        opened = []
        problems = []
        for endpoint in mirrors:
            try:
                device = enumerator.GetDevice(endpoint.id)
                renderer = wasapi.Renderer(device, capture.format)
                renderer.open()
                renderer.start()
                mirror = Mirror(endpoint, renderer, fmt.nSamplesPerSec,
                                fmt.nBlockAlign, fmt.wBitsPerSample,
                                delay_ms=delays.get(endpoint.id, 0),
                                gain=gains.get(endpoint.id, 1.0),
                                target_fill_ms=fill)
                mirror.prime()
                opened.append(mirror)
            except (comtypes.COMError, OSError) as exc:
                problems.append(f"{endpoint.name}: {exc}")

        if not opened:
            capture.stop()
            self.error = "; ".join(problems) or "no mirror devices could be opened"
            return False, self.error

        self.capture = capture
        self.mirrors = opened
        self.primary = primary
        self.format = fmt
        self.running.set()
        self.started_at = time.time()
        self.thread = threading.Thread(target=self._run, daemon=True,
                                       name="audio-router")
        self.thread.start()

        message = f"mirroring {primary.name} to {len(opened)} device(s)"
        if problems:
            message += " | failed: " + "; ".join(problems)
        return True, message

    def _run(self):
        comtypes.CoInitialize()
        priority = wasapi.raise_thread_priority()
        try:
            self.capture.start()
            while self.running.is_set():
                try:
                    data = self.capture.read()
                except Exception as exc:
                    self.error = f"capture stopped: {exc!r}"
                    break

                with self.lock:
                    for mirror in self.mirrors:
                        mirror.feed(data)
                        mirror.service()
                    if all(m.failed for m in self.mirrors):
                        self.error = ("all mirror streams were lost: "
                                      + "; ".join(m.failed for m in self.mirrors))
                        break
                time.sleep(SERVICE_INTERVAL)
        except Exception as exc:
            self.error = f"router thread crashed: {exc!r}"
        finally:
            wasapi.drop_thread_priority(priority)
            self._teardown()

    def _teardown(self):
        if self.capture is not None:
            self.capture.stop()
            self.capture = None
        with self.lock:
            for mirror in self.mirrors:
                mirror.close()
            self.mirrors = []

    def stop(self):
        if self.thread is not None and self.thread.is_alive():
            self.running.clear()
            self.thread.join(timeout=2.0)
        self.running.clear()
        self.thread = None
        self._teardown()
