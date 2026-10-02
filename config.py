"""Persisted settings: which devices are selected, per-mirror delay, hotkey.

Lives in %APPDATA% rather than next to the code so the project folder can be
moved or reinstalled without losing the setup.
"""

import json
import os

APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                       "audio-multiout")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")

DEFAULTS = {
    # Endpoint ids in order: the first is the primary, the rest are mirrors.
    "selected": [],
    # Endpoint id -> extra delay in ms, for lining a mirror up by ear.
    "delays": {},
    # Endpoint id -> digital boost multiplier for a mirror (1.0 = unity).
    "gains": {},
    # Route every checked device through the boost-capable mirror pipeline,
    # including what would otherwise be the native primary. See engine.py.
    "boost_all": False,
    # Endpoint id of the spare device used as a silent capture anchor when
    # boost_all is on. Picked automatically; see Engine._resolve_anchor.
    "boost_anchor_id": None,
    # Ctrl+Alt+V. Modifiers are MOD_ALT|MOD_CONTROL (0x1|0x2), vk is 'V'.
    "hotkey": {"modifiers": 0x0003, "vk": 0x56, "label": "Ctrl+Alt+V"},
    # Audio kept queued ahead on each mirror. Lower is tighter sync, higher
    # survives scheduling hiccups; 20ms is about the floor for Python.
    "target_fill_ms": 20,
    "set_windows_default": True,
    # Re-apply the saved selection when the app starts.
    "restore_on_start": True,
}


def load():
    settings = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
            stored = json.load(handle)
        if isinstance(stored, dict):
            settings.update({k: v for k, v in stored.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass  # first run, or the file got mangled -- defaults are fine
    return settings


def save(settings):
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as handle:
            json.dump(settings, handle, indent=2)
        return True, CONFIG_PATH
    except OSError as exc:
        return False, str(exc)
