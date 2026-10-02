"""Tray app: check several output devices, hear all of them at once.

Windows lets exactly one endpoint be the default, and its flyout is part of the
shell with no extension point -- so this replaces the flyout rather than
extending it. The multi-output is done in-process with WASAPI: the primary
device plays natively, and a loopback capture of it feeds every other device.

  python main.py            run the tray app
  python main.py --check    print diagnostics and exit
  python main.py --panel    show the panel immediately on launch
"""

import ctypes
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path

import comtypes

import config
import engine as engine_mod
import hotkey as hotkey_mod
import wasapi
import winaudio
from panel import Panel

APP_NAME = "Audio Multi-Out"
ASSETS_DIR = Path(__file__).resolve().parent / "assets"

ERROR_ALREADY_EXISTS = 183
# Session-local, so it guards this user's desktop without needing privileges.
_MUTEX_NAME = "Local\\audio-multiout-singleton"
_mutex_handle = None


def claim_single_instance():
    """True if this is the only instance.

    Auto-start plus a manual launch would otherwise leave two apps fighting
    over the hotkey and both mirroring, which doubles the audio. The handle is
    parked in a module global so it lives as long as the process.
    """
    global _mutex_handle
    _mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    return ctypes.windll.kernel32.GetLastError() != ERROR_ALREADY_EXISTS


_icon_cache = {}


def make_icon_image(active):
    """Tray glyph: a speaker, blue while more than one device is live.

    Loaded from a static .ico on disk (assets/gen_icons.py bakes it) rather
    than drawn fresh each call -- Windows keys the "always show in tray"
    setting off the icon's identity, and a freshly-rendered bitmap on every
    launch/state-change was making it treat the icon as new each time and
    silently re-hide it in the overflow.
    """
    if active not in _icon_cache:
        from PIL import Image

        name = "tray_active.ico" if active else "tray_inactive.ico"
        _icon_cache[active] = Image.open(ASSETS_DIR / name)
    return _icon_cache[active]


class App:
    def __init__(self, show_on_start=False):
        comtypes.CoInitialize()
        self.settings = config.load()
        self.engine = engine_mod.Engine(self.settings)
        self.events = queue.Queue()

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(APP_NAME)

        self.panel = Panel(self.root, self.engine, self.settings,
                           self.save_settings)

        self.hotkey = None
        self.tray = None
        self._start_hotkey()
        self._start_tray()

        if self.settings.get("restore_on_start", True) and self.settings.get("selected"):
            # Opening streams touches COM and can take a moment; keep it off
            # the UI thread so the panel stays responsive.
            threading.Thread(target=self._restore, daemon=True).start()

        self.root.after(80, self._pump)
        if show_on_start:
            self.root.after(200, self.panel.show)

    # -- wiring ------------------------------------------------------------

    def _restore(self):
        comtypes.CoInitialize()
        self.engine.apply(self.settings.get("selected", []))
        self.events.put(("engine-changed", None))

    def _start_hotkey(self):
        spec = self.settings.get("hotkey", config.DEFAULTS["hotkey"])
        self.hotkey = hotkey_mod.HotkeyListener(
            spec["modifiers"], spec["vk"],
            lambda: self.events.put(("toggle-panel", None)))
        self.hotkey.start()
        if not self.hotkey.wait_ready():
            # Not fatal -- the tray icon still opens the panel.
            print(f"[hotkey] {self.hotkey.error}", file=sys.stderr)

    def _start_tray(self):
        try:
            import pystray
        except ImportError:
            print("[tray] pystray not installed; tray icon disabled",
                  file=sys.stderr)
            return

        menu = pystray.Menu(
            pystray.MenuItem("Show panel",
                             lambda: self.events.put(("show-panel", None)),
                             default=True),
            pystray.MenuItem("Stop mirroring",
                             lambda: self.events.put(("stop-routing", None))),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda: self.events.put(("quit", None))),
        )
        self.tray = pystray.Icon("audio-multiout", make_icon_image(False),
                                 APP_NAME, menu)
        threading.Thread(target=self.tray.run, daemon=True).start()

    def _pump(self):
        """Drain events posted from the hotkey and tray threads."""
        try:
            while True:
                kind, _payload = self.events.get_nowait()
                if kind == "toggle-panel":
                    self.panel.toggle()
                elif kind == "show-panel":
                    self.panel.show()
                elif kind == "stop-routing":
                    self.engine.stop()
                    self._refresh_tray_icon()
                elif kind == "engine-changed":
                    if self.panel.visible:
                        self.panel.reload()
                    self._refresh_tray_icon()
                elif kind == "quit":
                    self.shutdown()
                    return
        except queue.Empty:
            pass
        self.root.after(80, self._pump)

    def _refresh_tray_icon(self):
        if self.tray is not None:
            self.tray.icon = make_icon_image(self.engine.active)

    # -- lifecycle ---------------------------------------------------------

    def save_settings(self):
        config.save(self.settings)
        self._refresh_tray_icon()

    def run(self):
        self.root.mainloop()

    def shutdown(self):
        self.save_settings()
        if self.tray is not None:
            self.tray.stop()
        if self.hotkey is not None:
            self.hotkey.stop()
        self.engine.stop()
        self.root.quit()
        self.root.destroy()


def check():
    """Diagnostics, so setup problems are visible without opening the UI."""
    comtypes.CoInitialize()
    print(f"{APP_NAME} -- diagnostics\n")

    endpoints = winaudio.list_outputs()
    print(f"Windows playback endpoints ({len(endpoints)}):")
    for endpoint in endpoints:
        print(f"  {'*' if endpoint.is_default else ' '} {endpoint.name}")

    default = winaudio.current_default()
    if default is None:
        print("\nNo default output -- nothing to capture.")
        return 1

    print(f"\nLoopback probe on: {default.name}")
    enumerator = winaudio._enumerator()
    capture = wasapi.LoopbackCapture(enumerator.GetDevice(default.id))
    try:
        fmt = capture.open()
    except (comtypes.COMError, OSError) as exc:
        print(f"  FAILED to open loopback: {exc}")
        return 1
    print(f"  mix format: {fmt.describe()}")

    print("\nRender probe on the other endpoints:")
    opened = 0
    for endpoint in endpoints:
        if endpoint.id == default.id:
            continue
        try:
            renderer = wasapi.Renderer(enumerator.GetDevice(endpoint.id),
                                       capture.format)
            frames = renderer.open()
            latency_ms = frames * 1000.0 / fmt.nSamplesPerSec
            print(f"  OK   {endpoint.name}  (buffer {frames} frames"
                  f" = {latency_ms:.0f}ms)")
            renderer.stop()
            opened += 1
        except (comtypes.COMError, OSError) as exc:
            print(f"  FAIL {endpoint.name}: {exc}")

    print("\nThroughput check (1s of loopback)...")
    capture.start()
    priority = wasapi.raise_thread_priority()
    total, start = 0, time.time()
    while time.time() - start < 1.0:
        total += len(capture.read())
        time.sleep(0.004)
    wasapi.drop_thread_priority(priority)
    capture.stop()
    seconds = total / max(fmt.nAvgBytesPerSec, 1)
    print(f"  captured {seconds:.2f}s of audio in 1.0s wall"
          f"  (Pro Audio priority: {'yes' if priority else 'no'})")

    print(f"\nReady: {opened} device(s) can mirror the primary.")
    return 0 if opened else 1


def main(argv):
    if "--check" in argv:
        return check()
    if not claim_single_instance():
        print(f"{APP_NAME} is already running (check the tray).", file=sys.stderr)
        return 0
    App(show_on_start="--panel" in argv).run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
