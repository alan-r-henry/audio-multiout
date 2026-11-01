"""Launch-at-login via the per-user Run key (no admin rights, no Task Scheduler).

The registry is the single source of truth: `is_enabled()` reads it directly, so
the tray checkbox can never drift out of step with what Windows will actually do.
"""

import os
import sys
import winreg

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "AudioMultiOut"


def launch_command(main_path=None):
    """The exact command line Windows should run at login.

    Uses pythonw.exe so no console window flashes up, and absolute paths so it
    doesn't depend on the working directory Windows starts it in.
    """
    if getattr(sys, "frozen", False):  # PyInstaller build
        return f'"{sys.executable}"'
    interpreter = sys.executable
    head, tail = os.path.split(interpreter)
    if tail.lower() == "python.exe":
        windowless = os.path.join(head, "pythonw.exe")
        if os.path.exists(windowless):
            interpreter = windowless
    script = main_path or os.path.abspath(sys.argv[0])
    return f'"{interpreter}" "{script}"'


# A shortcut the user (or an earlier install) may have dropped in the Startup
# folder. It launches the app just as well, so it counts as "enabled" -- and
# turning the option off must remove it too, or the app would still start.
SHORTCUT_NAME = "Audio Multi-Out.lnk"


def startup_shortcut_path():
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return os.path.join(appdata, "Microsoft", "Windows", "Start Menu",
                        "Programs", "Startup", SHORTCUT_NAME)


def _has_shortcut(name):
    # Only the real app name maps to the shortcut; test names must not.
    path = startup_shortcut_path()
    return name == VALUE_NAME and bool(path) and os.path.exists(path)


def _has_run_value(name):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, name)
            return True
    except OSError:
        return False


def is_enabled(name=VALUE_NAME):
    return _has_run_value(name) or _has_shortcut(name)


def enable(command=None, name=VALUE_NAME):
    """Returns (ok, note). A Startup shortcut already does the job."""
    if _has_shortcut(name):
        return True, "already starts with Windows (Startup shortcut)"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command or launch_command())
        return True, "will start with Windows"
    except OSError as exc:
        return False, f"could not enable start-with-Windows: {exc}"


def disable(name=VALUE_NAME):
    removed = False
    if _has_shortcut(name):
        try:
            os.remove(startup_shortcut_path())
            removed = True
        except OSError as exc:
            return False, f"could not remove the Startup shortcut: {exc}"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
        removed = True
    except FileNotFoundError:
        pass
    except OSError as exc:
        return False, f"could not disable start-with-Windows: {exc}"
    return True, ("will no longer start with Windows" if removed
                  else "was not enabled")


def toggle(name=VALUE_NAME):
    return disable(name) if is_enabled(name) else enable(name=name)
