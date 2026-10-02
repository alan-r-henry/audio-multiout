"""System-wide hotkey via RegisterHotKey.

Runs on its own thread with its own message loop. Passing a null window handle
makes Windows post WM_HOTKEY to the thread queue directly, so no hidden window
is needed -- and unlike a low-level keyboard hook this does not require the app
to run elevated.
"""

import ctypes
import threading
from ctypes import wintypes

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

_user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int,
                                   wintypes.UINT, wintypes.UINT]
_user32.RegisterHotKey.restype = wintypes.BOOL
_user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                wintypes.UINT, wintypes.UINT]
_user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT,
                                       wintypes.WPARAM, wintypes.LPARAM]

HOTKEY_ID = 0xA10


class HotkeyListener(threading.Thread):
    def __init__(self, modifiers, vk, callback):
        super().__init__(daemon=True, name="hotkey")
        self.modifiers = modifiers | MOD_NOREPEAT
        self.vk = vk
        self.callback = callback
        self.thread_id = None
        self.error = None
        self._ready = threading.Event()

    def run(self):
        self.thread_id = _kernel32.GetCurrentThreadId()

        if not _user32.RegisterHotKey(None, HOTKEY_ID, self.modifiers, self.vk):
            # Almost always means another app already owns the combination.
            self.error = (f"could not register hotkey "
                          f"(error {ctypes.get_last_error() or 'unknown'})")
            self._ready.set()
            return

        self._ready.set()
        message = wintypes.MSG()
        try:
            while _user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == WM_HOTKEY and message.wParam == HOTKEY_ID:
                    try:
                        self.callback()
                    except Exception:  # a UI error must not kill the listener
                        pass
        finally:
            _user32.UnregisterHotKey(None, HOTKEY_ID)

    def wait_ready(self, timeout=3.0):
        self._ready.wait(timeout)
        return self.error is None

    def stop(self):
        if self.thread_id:
            _user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)


def describe(modifiers, vk):
    parts = []
    if modifiers & MOD_CONTROL:
        parts.append("Ctrl")
    if modifiers & MOD_ALT:
        parts.append("Alt")
    if modifiers & MOD_SHIFT:
        parts.append("Shift")
    if modifiers & MOD_WIN:
        parts.append("Win")
    parts.append(chr(vk) if 0x30 <= vk <= 0x5A else f"VK{vk:02X}")
    return "+".join(parts)
