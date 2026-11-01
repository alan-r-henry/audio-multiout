"""Small Win32 UI helpers: DPI awareness and where to put the popup.

The panel used to assume a 56px taskbar along the bottom of a 96-DPI screen. On
a scaled display Windows bitmap-stretched the whole window (blurry), and a
taskbar on another edge or of another height put it in the wrong place.
"""

import ctypes
from ctypes import wintypes

SPI_GETWORKAREA = 0x0030
BASE_DPI = 96


def enable_dpi_awareness():
    """Opt in to per-monitor DPI so Tk renders sharp. Call before creating Tk.

    Returns True if awareness was set. Failure is harmless (the window is just
    scaled by Windows), so every error is swallowed.
    """
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor
        return True
    except (OSError, AttributeError):
        pass
    try:
        return bool(ctypes.windll.user32.SetProcessDPIAware())
    except (OSError, AttributeError):
        return False


def work_area():
    """(left, top, right, bottom) of the primary monitor minus the taskbar."""
    rect = wintypes.RECT()
    if ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
        return rect.left, rect.top, rect.right, rect.bottom
    return (0, 0, ctypes.windll.user32.GetSystemMetrics(0),
            ctypes.windll.user32.GetSystemMetrics(1))


def screen_bounds():
    return (0, 0, ctypes.windll.user32.GetSystemMetrics(0),
            ctypes.windll.user32.GetSystemMetrics(1))


def place_popup(area, screen, width, height, gap=12):
    """Top-left (x, y) for a popup anchored to the tray corner.

    `area` is the work area (screen minus taskbar). The popup hugs the
    bottom-right of it, which is where the tray sits for a bottom taskbar --
    and also (right edge, near the bottom) for a left/right one. With the
    taskbar along the top, the tray is at the top-right instead, so the popup
    drops from there. It is clamped to stay fully on screen.
    """
    left, top, right, bottom = area
    s_left, s_top, s_right, s_bottom = screen
    taskbar_on_top = top > s_top and bottom >= s_bottom
    x = right - width - gap
    y = (top + gap) if taskbar_on_top else (bottom - height - gap)
    x = max(s_left, min(x, s_right - width))
    y = max(s_top, min(y, s_bottom - height))
    return x, y


def scale_factor(dpi):
    return max(1.0, dpi / BASE_DPI)
