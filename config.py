"""Persisted settings: which devices are selected, per-mirror delay, hotkey.

Lives in %APPDATA% rather than next to the code so the project folder can be
moved or reinstalled without losing the setup.
"""

import copy
import json
import os

# Mirrors router.MAX_GAIN; duplicated so config stays importable without
# pulling in the COM stack. tests/test_config.py asserts they agree.
MAX_GAIN = 3.0

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
    # Endpoint id -> last seen friendly name. Windows regenerates endpoint ids
    # on driver/OS updates; the name lets a vanished id be re-matched to the
    # same device. See Engine._reconcile_ids.
    "device_names": {},
    # Ctrl+Alt+V. Modifiers are MOD_ALT|MOD_CONTROL (0x1|0x2), vk is 'V'.
    "hotkey": {"modifiers": 0x0003, "vk": 0x56, "label": "Ctrl+Alt+V"},
    # Audio kept queued ahead on each mirror. Lower is tighter sync, higher
    # survives scheduling hiccups; 20ms is about the floor for Python.
    "target_fill_ms": 20,
    "set_windows_default": True,
    # Re-apply the saved selection when the app starts.
    "restore_on_start": True,
}


def _sanitize(settings):
    """Drop stored values of the wrong type or out of range.

    The file is hand-editable and may predate the current schema; one bad
    value (a string where a list belongs) should cost that setting, not crash
    the app on start.
    """
    for key, default in DEFAULTS.items():
        value = settings[key]
        if isinstance(default, bool):
            ok = isinstance(value, bool)
        elif isinstance(default, int):
            ok = isinstance(value, int) and not isinstance(value, bool)
        elif default is None:
            ok = value is None or isinstance(value, str)
        else:
            ok = isinstance(value, type(default))
        if not ok:
            settings[key] = copy.deepcopy(default)

    settings["selected"] = [i for i in settings["selected"] if isinstance(i, str)]
    settings["target_fill_ms"] = max(5, min(200, settings["target_fill_ms"]))
    settings["delays"] = {
        k: max(0, min(500, int(v))) for k, v in settings["delays"].items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)}
    settings["gains"] = {
        k: max(0.0, min(MAX_GAIN, float(v))) for k, v in settings["gains"].items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)}
    settings["device_names"] = {
        k: v for k, v in settings["device_names"].items() if isinstance(v, str)}
    hotkey = settings["hotkey"]
    if not (isinstance(hotkey.get("modifiers"), int) and isinstance(hotkey.get("vk"), int)):
        settings["hotkey"] = copy.deepcopy(DEFAULTS["hotkey"])
    return settings


def load():
    settings = copy.deepcopy(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
            stored = json.load(handle)
        if isinstance(stored, dict):
            settings.update({k: v for k, v in stored.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass  # first run, or the file got mangled -- defaults are fine
    return _sanitize(settings)


def save(settings):
    """Write atomically: a crash mid-write must not leave a truncated file."""
    tmp_path = CONFIG_PATH + ".tmp"
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(settings, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, CONFIG_PATH)
        return True, CONFIG_PATH
    except OSError as exc:
        return False, str(exc)
