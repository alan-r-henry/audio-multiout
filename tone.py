"""Test tone: prove a device can make sound, independent of any routing.

When "no audio from the monitor" happens, the first question is whether the PC
is delivering signal at all. This plays a short beep straight to one endpoint
through an ordinary WASAPI stream (no loopback, no mirroring) and watches that
endpoint's peak meter while it plays:

  * meter moved, you heard it   -> device is fine; the problem is upstream
  * meter moved, you heard nothing -> the PC delivered it; the device, cable or
    monitor firmware ate it (unplug the monitor's power for 30s)
  * meter flat                  -> the PC never delivered it (muted/driver)
"""

import time

import comtypes
import numpy as np

import applog
import hresult
import wasapi
import winaudio

log = applog.get("tone")

DEFAULT_SECONDS = 1.2
DEFAULT_AMPLITUDE = 0.25  # quiet enough not to startle headphone users
METER_PASS = 0.02         # peak above this counts as "signal reached the device"


def make_tone(sample_rate, channels, bits_per_sample, seconds=DEFAULT_SECONDS,
              freq=440.0, amplitude=DEFAULT_AMPLITUDE, fade_s=0.05):
    """Interleaved sine with short fades (no click). None for unsupported bits."""
    frames = int(sample_rate * seconds)
    t = np.arange(frames, dtype=np.float64) / sample_rate
    wave = amplitude * np.sin(2 * np.pi * freq * t)
    fade = max(1, int(sample_rate * fade_s))
    ramp = np.linspace(0.0, 1.0, fade)
    wave[:fade] *= ramp
    wave[-fade:] *= ramp[::-1]
    interleaved = np.repeat(wave[:, None], channels, axis=1)
    if bits_per_sample == 32:
        return interleaved.astype("<f4").tobytes()
    if bits_per_sample == 16:
        return np.rint(interleaved * 32767).astype("<i2").tobytes()
    return None


def interpret(peak, baseline=0.0):
    """Plain-language verdict from the highest meter reading seen.

    `baseline` is the peak measured just before the tone. If other audio was
    already reaching the device the meter can't prove the tone got through.
    """
    if peak is None:
        return False, "this device has no level meter, so delivery can't be checked"
    if baseline >= METER_PASS:
        return True, ("other audio is already playing on this device, so the meter "
                      "can't isolate the tone -- listen for the beep")
    if peak >= METER_PASS:
        return True, (f"tone delivered (meter peaked at {peak:.2f}). If you heard "
                      "nothing, the PC did its job -- check the device, cable, or "
                      "power-cycle the monitor")
    return False, ("tone was NOT delivered (meter stayed flat) -- check the "
                   "device is enabled and not muted in Windows")


def _baseline(meter, window=0.15):
    """Highest level on the device right now (before the tone), or None."""
    if meter.peak() is None:
        return None
    end, high = time.monotonic() + window, 0.0
    while time.monotonic() < end:
        high = max(high, meter.peak() or 0.0)
        time.sleep(0.01)
    return high


def play(device_id, seconds=DEFAULT_SECONDS):
    """Play the tone on one endpoint. Blocking; run on a worker thread.

    Returns (ok, message). Never raises.
    """
    try:
        comtypes.CoInitialize()
        device = winaudio._enumerator().GetDevice(device_id)
        fmt = wasapi.mix_format(device)
        rate, channels = fmt.contents.nSamplesPerSec, fmt.contents.nChannels
        bits = fmt.contents.wBitsPerSample
        data = make_tone(rate, channels, bits, seconds)
        if data is None:
            return False, f"unsupported device format ({bits}-bit)"

        meter = winaudio.PeakMeter(device_id)
        renderer = wasapi.Renderer(device, fmt)
        renderer.open()
        renderer.start()
        queue = memoryview(data)
        baseline = _baseline(meter)
        peak = None if baseline is None else 0.0
        deadline = time.monotonic() + seconds + 2.0
        try:
            while time.monotonic() < deadline:
                if len(queue):
                    queue = queue[renderer.write(queue):]
                reading = meter.peak()
                if reading is not None:
                    peak = max(peak or 0.0, reading)
                if not len(queue) and renderer.padding_frames() == 0:
                    break
                time.sleep(0.01)
        finally:
            renderer.stop()
        ok, message = interpret(peak, baseline or 0.0)
        log.info("test tone on %s: %s", device_id, message)
        return ok, message
    except (OSError, comtypes.COMError) as exc:
        log.warning("test tone failed on %s: %s", device_id, exc)
        return False, f"could not play to this device: {hresult.describe(exc)}"
