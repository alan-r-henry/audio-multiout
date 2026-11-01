"""Plain-English reasons for the COM errors audio code actually hits.

A status line reading "(-2004287484, 'Unknown error 0x88890004', ...)" tells the
user nothing. These are the failures that really occur when opening streams on
real hardware, with the likely cause and what to try.
"""

_MESSAGES = {
    0x88890004: "device was unplugged, disabled, or its driver restarted",
    0x88890008: "device doesn't support the primary's audio format",
    0x8889000A: "device is in use by another app (exclusive mode)",
    0x8889000E: "exclusive mode isn't allowed on this device",
    0x8889000F: "Windows couldn't create the stream for this device",
    0x88890010: "the Windows Audio service isn't running",
    0x88890002: "stream was already initialised",
    0x80070005: "access denied by Windows",
    0x80070490: "device not found (removed or renamed)",
    0x8007001F: "device isn't working properly",
    0x80004005: "unspecified error from the audio driver",
    0x80070057: "invalid argument passed to the audio driver",
}


def code_of(exc):
    """The unsigned HRESULT inside an exception, or None."""
    value = None
    if getattr(exc, "hresult", None) is not None:  # comtypes.COMError
        value = exc.hresult
    elif getattr(exc, "winerror", None) is not None:  # OSError from ctypes
        value = exc.winerror
    elif exc.args and isinstance(exc.args[0], int):
        value = exc.args[0]
    return None if value is None else value & 0xFFFFFFFF


def describe(exc):
    """Short human reason for `exc`, falling back to the hex code."""
    code = code_of(exc)
    if code is None:
        return str(exc) or exc.__class__.__name__
    reason = _MESSAGES.get(code)
    return f"{reason} (0x{code:08X})" if reason else f"error 0x{code:08X}"
