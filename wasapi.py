"""WASAPI: loopback capture from a render endpoint, and rendering to others.

This is what lets the app fan audio out to several devices without a virtual
audio driver. Opening a *render* endpoint with AUDCLNT_STREAMFLAGS_LOOPBACK
hands back exactly the mix that endpoint is playing, which we then push into
ordinary render streams on the other endpoints.

Targets are initialised with the *source's* mix format plus AUTOCONVERTPCM, so
Windows does any sample-rate and channel conversion in its own resampler. That
removes the need to write one here and keeps mismatched devices working.
"""

import ctypes
from ctypes import POINTER, Structure
from ctypes.wintypes import DWORD, HANDLE, UINT, WORD

import comtypes
from comtypes import COMMETHOD, GUID, IUnknown

from winaudio import IMMDevice  # noqa: F401  (re-exported for callers)

AUDCLNT_SHAREMODE_SHARED = 0

AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_STREAMFLAGS_EVENTCALLBACK = 0x00040000
AUDCLNT_STREAMFLAGS_NOPERSIST = 0x00080000
AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM = 0x80000000
AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY = 0x08000000

AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY = 0x1
AUDCLNT_BUFFERFLAGS_SILENT = 0x2

# REFERENCE_TIME is in 100-nanosecond units.
REFTIMES_PER_MS = 10000

AUDCLNT_E_DEVICE_INVALIDATED = -2004287484  # 0x88890004
AUDCLNT_E_UNSUPPORTED_FORMAT = -2004287480  # 0x88890008


class WAVEFORMATEX(Structure):
    _fields_ = [
        ("wFormatTag", WORD),
        ("nChannels", WORD),
        ("nSamplesPerSec", DWORD),
        ("nAvgBytesPerSec", DWORD),
        ("nBlockAlign", WORD),
        ("wBitsPerSample", WORD),
        ("cbSize", WORD),
    ]

    def describe(self):
        return (f"{self.nSamplesPerSec} Hz, {self.nChannels} ch, "
                f"{self.wBitsPerSample}-bit")


class IAudioRenderClient(IUnknown):
    _iid_ = GUID("{F294ACFC-3146-4483-A7BF-ADDCA7C260E2}")
    _methods_ = [
        COMMETHOD([], comtypes.HRESULT, "GetBuffer",
                  (["in"], UINT, "NumFramesRequested"),
                  (["out"], POINTER(POINTER(ctypes.c_ubyte)), "ppData")),
        COMMETHOD([], comtypes.HRESULT, "ReleaseBuffer",
                  (["in"], UINT, "NumFramesWritten"),
                  (["in"], DWORD, "dwFlags")),
    ]


class IAudioCaptureClient(IUnknown):
    _iid_ = GUID("{C8ADBD64-E71E-48A0-A4DE-185C395CD317}")
    _methods_ = [
        COMMETHOD([], comtypes.HRESULT, "GetBuffer",
                  (["out"], POINTER(POINTER(ctypes.c_ubyte)), "ppData"),
                  (["out"], POINTER(UINT), "pNumFramesToRead"),
                  (["out"], POINTER(DWORD), "pdwFlags"),
                  (["out"], POINTER(ctypes.c_ulonglong), "pu64DevicePosition"),
                  (["out"], POINTER(ctypes.c_ulonglong), "pu64QPCPosition")),
        COMMETHOD([], comtypes.HRESULT, "ReleaseBuffer",
                  (["in"], UINT, "NumFramesRead")),
        COMMETHOD([], comtypes.HRESULT, "GetNextPacketSize",
                  (["out"], POINTER(UINT), "pNumFramesInNextPacket")),
    ]


class IAudioClient(IUnknown):
    _iid_ = GUID("{1CB9AD4C-DBFA-4C32-B178-C2F568A703B2}")
    _methods_ = [
        COMMETHOD([], comtypes.HRESULT, "Initialize",
                  (["in"], DWORD, "ShareMode"),
                  (["in"], DWORD, "StreamFlags"),
                  (["in"], ctypes.c_longlong, "hnsBufferDuration"),
                  (["in"], ctypes.c_longlong, "hnsPeriodicity"),
                  (["in"], POINTER(WAVEFORMATEX), "pFormat"),
                  (["in"], POINTER(GUID), "AudioSessionGuid")),
        COMMETHOD([], comtypes.HRESULT, "GetBufferSize",
                  (["out"], POINTER(UINT), "pNumBufferFrames")),
        COMMETHOD([], comtypes.HRESULT, "GetStreamLatency",
                  (["out"], POINTER(ctypes.c_longlong), "phnsLatency")),
        COMMETHOD([], comtypes.HRESULT, "GetCurrentPadding",
                  (["out"], POINTER(UINT), "pNumPaddingFrames")),
        COMMETHOD([], comtypes.HRESULT, "IsFormatSupported",
                  (["in"], DWORD, "ShareMode"),
                  (["in"], POINTER(WAVEFORMATEX), "pFormat"),
                  (["out"], POINTER(POINTER(WAVEFORMATEX)), "ppClosestMatch")),
        COMMETHOD([], comtypes.HRESULT, "GetMixFormat",
                  (["out"], POINTER(POINTER(WAVEFORMATEX)), "ppDeviceFormat")),
        COMMETHOD([], comtypes.HRESULT, "GetDevicePeriod",
                  (["out"], POINTER(ctypes.c_longlong), "phnsDefaultDevicePeriod"),
                  (["out"], POINTER(ctypes.c_longlong), "phnsMinimumDevicePeriod")),
        COMMETHOD([], comtypes.HRESULT, "Start"),
        COMMETHOD([], comtypes.HRESULT, "Stop"),
        COMMETHOD([], comtypes.HRESULT, "Reset"),
        COMMETHOD([], comtypes.HRESULT, "SetEventHandle",
                  (["in"], HANDLE, "eventHandle")),
        COMMETHOD([], comtypes.HRESULT, "GetService",
                  (["in"], POINTER(GUID), "riid"),
                  (["out"], POINTER(POINTER(IUnknown)), "ppv")),
    ]


def _activate(device, interface):
    """IMMDevice::Activate, cast to the interface we actually want."""
    raw = device.Activate(interface._iid_, comtypes.CLSCTX_ALL, None)
    return raw.QueryInterface(interface)


def mix_format(device):
    """The endpoint's shared-mode mix format. Caller owns nothing to free."""
    client = _activate(device, IAudioClient)
    return client.GetMixFormat()


class LoopbackCapture:
    """Receives whatever a render endpoint is currently playing.

    Loopback streams only produce packets while something is playing; silence
    shows up as either no packet at all or a packet flagged SILENT. Callers
    must treat "no data" as silence rather than as an error.
    """

    def __init__(self, device, buffer_ms=60):
        self.device = device
        self.buffer_ms = buffer_ms
        self.client = None
        self.capture = None
        self.format = None
        self.frame_bytes = 0

    def open(self):
        self.client = _activate(self.device, IAudioClient)
        self.format = self.client.GetMixFormat()
        self.frame_bytes = self.format.contents.nBlockAlign

        self.client.Initialize(
            AUDCLNT_SHAREMODE_SHARED,
            AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_NOPERSIST,
            self.buffer_ms * REFTIMES_PER_MS,
            0,
            self.format,
            None)
        self.capture = self.client.GetService(
            IAudioCaptureClient._iid_).QueryInterface(IAudioCaptureClient)
        return self.format.contents

    def start(self):
        self.client.Start()

    def stop(self):
        try:
            self.client.Stop()
        except (comtypes.COMError, OSError):
            pass

    def read(self):
        """Pull all queued packets. Returns bytes (possibly empty)."""
        chunks = []
        while True:
            try:
                available = self.capture.GetNextPacketSize()
            except comtypes.COMError:
                break
            if not available:
                break

            data, frames, flags, _pos, _qpc = self.capture.GetBuffer()
            if frames:
                nbytes = frames * self.frame_bytes
                if flags & AUDCLNT_BUFFERFLAGS_SILENT:
                    chunks.append(b"\x00" * nbytes)
                else:
                    chunks.append(ctypes.string_at(data, nbytes))
            self.capture.ReleaseBuffer(frames)
        return b"".join(chunks)


class Renderer:
    """Plays a byte stream to one endpoint, converting format as needed."""

    def __init__(self, device, source_format, buffer_ms=80):
        self.device = device
        self.source_format = source_format
        self.buffer_ms = buffer_ms
        self.client = None
        self.render = None
        self.buffer_frames = 0
        self.frame_bytes = source_format.contents.nBlockAlign

    def open(self):
        self.client = _activate(self.device, IAudioClient)
        # AUTOCONVERTPCM lets Windows resample the source format to whatever
        # this endpoint actually runs at, so mismatched devices just work.
        self.client.Initialize(
            AUDCLNT_SHAREMODE_SHARED,
            (AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM
             | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY
             | AUDCLNT_STREAMFLAGS_NOPERSIST),
            self.buffer_ms * REFTIMES_PER_MS,
            0,
            self.source_format,
            None)
        self.buffer_frames = self.client.GetBufferSize()
        self.render = self.client.GetService(
            IAudioRenderClient._iid_).QueryInterface(IAudioRenderClient)
        return self.buffer_frames

    def start(self):
        self.client.Start()

    def stop(self):
        try:
            self.client.Stop()
        except (comtypes.COMError, OSError):
            pass

    def space_frames(self):
        """Frames that can be written right now without blocking."""
        return self.buffer_frames - self.client.GetCurrentPadding()

    def padding_frames(self):
        return self.client.GetCurrentPadding()

    def write(self, data):
        """Write as many whole frames of `data` as fit. Returns bytes consumed.

        `data` may be any bytes-like object. memmove will not take a memoryview
        directly, and holding a ctypes view onto the caller's bytearray would
        block it from trimming the buffer afterwards, so the slice is copied.
        """
        frames = len(data) // self.frame_bytes
        if not frames:
            return 0
        room = self.space_frames()
        if room <= 0:
            return 0
        frames = min(frames, room)
        nbytes = frames * self.frame_bytes

        chunk = bytes(memoryview(data)[:nbytes])
        pointer = self.render.GetBuffer(frames)
        ctypes.memmove(pointer, chunk, nbytes)
        self.render.ReleaseBuffer(frames, 0)
        return nbytes

    def write_silence(self, frames):
        frames = min(frames, max(self.space_frames(), 0))
        if frames <= 0:
            return 0
        pointer = self.render.GetBuffer(frames)
        self.render.ReleaseBuffer(frames, AUDCLNT_BUFFERFLAGS_SILENT)
        return frames * self.frame_bytes


_avrt = None


def raise_thread_priority():
    """Ask MMCSS to treat this thread as pro audio.

    Without it, Python's scheduling under load produces audible dropouts.
    Returns a handle to pass to drop_thread_priority, or None.
    """
    global _avrt
    try:
        if _avrt is None:
            _avrt = ctypes.windll.avrt
        task_index = DWORD(0)
        handle = _avrt.AvSetMmThreadCharacteristicsW(
            ctypes.c_wchar_p("Pro Audio"), ctypes.byref(task_index))
        return handle or None
    except (OSError, AttributeError):
        return None


def drop_thread_priority(handle):
    if handle and _avrt is not None:
        try:
            _avrt.AvRevertMmThreadCharacteristics(handle)
        except OSError:
            pass
