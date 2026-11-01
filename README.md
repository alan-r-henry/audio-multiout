# Audio Multi-Out

A replacement for the Windows 11 sound flyout that lets you check **more than one
output device at once** and hear all of them simultaneously.

No third-party audio software, no virtual audio driver, no reboot. The routing
is done in-process with WASAPI.

## Why it is a replacement and not an addition

The stock flyout cannot be extended. It is part of `ShellExperienceHost` and
Microsoft exposes no extension point, and underneath it Windows only ever has
one default endpoint. So this is a separate panel on a global hotkey.

## How it works

The first device you check is the **primary**. It becomes the Windows default
and plays through the normal path -- we never touch it, so it has **zero added
latency**.

Every other checked device is a **mirror**. A WASAPI loopback capture on the
primary hands us exactly what it is playing, and we render those frames to each
mirror. Mirrors are initialised with the primary's mix format plus
`AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM`, so Windows resamples for devices running
at a different rate or channel count.

```
  apps ──▶ Headphones (primary, native path, 0ms)
              │
              └─ loopback capture ──▶ BenQ EX2710S   (mirror, ~20ms)
                                  └─▶ S/PDIF          (mirror, ~20ms)
```

### Drift

The primary and each mirror run on independent hardware clocks, so a mirror
slowly gains or loses frames -- a few parts per million, but enough to overflow
or empty a buffer over minutes. `router.py` measures how much audio is in flight
per mirror, smooths it, and nudges by at most 1ms twice a second when the
smoothed depth drifts more than 10ms off target.

In steady state it corrects **nothing** -- measured over 10s of live mirroring:
zero corrections, zero dropped frames, zero inserted frames. Correction only
engages once real clock drift accumulates.

## Setup

Already done in `.venv`. From scratch it is:

```bash
python -m venv .venv && .venv\Scripts\pip install -r requirements.txt
```

Check the audio stack before using the GUI:

```bash
.venv\Scripts\python.exe main.py --check
```

That prints your endpoints, probes loopback on the default, opens a render
stream on every other endpoint, and measures capture throughput. It is the
fastest way to see which layer is broken.

Run it:

```bash
.venv\Scripts\pythonw.exe main.py
```

`pythonw` keeps it console-free. Use `python main.py` to see tracebacks and
hotkey warnings.

## Using it

Press **Ctrl+Alt+V** (or click the tray icon) to open the panel. Check the
devices you want; changes apply immediately.

- **Order matters.** First checked is the primary (tagged `PRIM`), the rest are
  mirrors (`MIR`).
- Checking exactly **one** device is just a fast default-device switch -- no
  capture runs at all.
- The **ms** box adds delay to a mirror. The live figure beside it is that
  mirror's current offset behind the primary.
- Click a device's `MIR` tag to make it the primary without re-checking.
- **Vol** slider is Windows' own per-device volume (follows changes made
  elsewhere); click the **Vol** caption to mute that device. Mouse wheel nudges
  any slider; double-click **Boost** to reset it to 100%.
- **Boost** (mirrors only, up to 300%) is digital gain with a soft-knee limiter,
  so loud peaks are squashed rather than chopped. The percentage turns amber
  while the limiter is working -- more boost past that point only adds
  distortion.
- The thin bar under each device is a **live level meter** of what Windows is
  sending to that endpoint. **Test** plays a short tone to one device and says
  whether the PC delivered it -- the quick way to tell "PC sent audio" from "the
  monitor/cable swallowed it" (for a monitor that goes silent, unplug its power
  cable for 30s).
- Tray menu: **Devices** (check/uncheck outputs without opening the panel),
  **Stop mirroring**, **Start with Windows**.

### Self-healing

Every few seconds the app checks that routing is still what you asked for. If a
selected device is unplugged or plugged back in, or a stream dies, it re-applies
automatically (backing off if the device is genuinely gone). Windows renumbers
endpoint ids on driver/OS updates; the app remembers each device's name and
re-attaches your selection, delays and boosts to the same device afterwards.
In boost-all mode, stopping or quitting hands the Windows default back to your
first checked device instead of leaving it on the silent anchor.

## What this cannot do

**Mirrors always lag the primary**, by roughly the queue depth (20ms default,
tunable via `target_fill_ms` in the config). The delay box only ever *adds*
more. There is no way to delay the primary, because the primary is the native
path and there is nothing of ours in it to hold back.

That matters if your monitor's built-in speakers are inherently slower than your
headphones -- a mirror can be pushed later to match a slow device, but a slow
primary cannot be pushed later to match a fast mirror. If you hit that case, make
the *slow* device the primary and mirror the fast one.

Other limits:

- If an app takes the primary endpoint in **exclusive mode**, loopback capture
  stops receiving and mirrors go silent until it releases.
- Real-time audio in Python means dropouts are possible under heavy load. The
  router runs at MMCSS "Pro Audio" priority to make that rare, and 20ms of queue
  absorbs normal scheduling jitter. Raise `target_fill_ms` if you hear
  stuttering.

## Layout

| File | Does |
|---|---|
| `main.py` | Tray icon + menu, hotkey wiring, event pump, self-heal timer, `--check` |
| `panel.py` | The popup: devices, volume/mute, boost, meters, test tone |
| `engine.py` | Checked list -> primary + mirrors; id re-matching; recovery |
| `router.py` | Capture thread, per-mirror queues, drift correction, boost limiter |
| `wasapi.py` | `IAudioClient` / loopback capture / render, via ctypes COM |
| `winaudio.py` | Core Audio enumeration, default switching, volume/mute/meter |
| `tone.py` | Test tone + "did the PC deliver it?" verdict |
| `config.py` | Validated settings, atomic save, `%APPDATA%\audio-multiout\config.json` |
| `applog.py` | Rotating log at `%APPDATA%\audio-multiout\app.log` |
| `autostart.py` | Start-with-Windows (Run key / existing Startup shortcut) |
| `hresult.py` | Plain-English reasons for COM errors |
| `winui.py` | DPI awareness and work-area-aware popup placement |
| `tests/` | Unit tests (`python -m unittest discover -s tests -t .`) |

## Notes

`winaudio.set_default_output` goes through **IPolicyConfig**, a COM interface
Microsoft has never documented. It has been stable since Windows 7 and is what
every third-party audio switcher uses, but it is not supported API. Every call
is guarded; if a Windows update breaks it, the panel reports the failure and you
can still switch the default by hand.

Settings persist to `%APPDATA%\audio-multiout\config.json`. Delete it to start
over. `restore_on_start` re-applies your last selection on launch; set it to
`false` if you would rather opt in each time.

## Startup

Use the tray menu's **Start with Windows** (it also recognises an existing
`Audio Multi-Out.lnk` in the Startup folder, and removes it when you turn the
option off).

## Troubleshooting

Errors are logged to `%APPDATA%\audio-multiout\app.log` (the app runs without
a console). `main.py --check` prints every device's volume, mute state and live
level, whether your saved selection still resolves, and probes loopback.

## Tests

```bash
.venv\Scripts\python.exe -m unittest discover -s tests -t .
```

Pure logic (gain/limiter, drift, config validation, id re-matching, recovery
backoff, placement math) runs anywhere; a few tests touch the real audio stack
and skip themselves if there is no output device.
