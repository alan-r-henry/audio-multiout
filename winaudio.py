"""Windows Core Audio endpoint enumeration and default-device switching.

Two jobs: list the playback endpoints Windows knows about (so the panel can show
real names rather than whatever VoiceMeeter happens to report), and point the
system default at the VoiceMeeter virtual input once routing is set up.

The default-device switch goes through IPolicyConfig, which Microsoft has never
documented. It has been stable since Windows 7 and is what every third-party
audio switcher uses, but it is not a supported API -- so every call here is
guarded and failure is reported to the caller rather than raised.
"""

import ctypes
from ctypes import POINTER, Structure, Union, c_ulonglong, c_ushort, c_wchar_p
from ctypes.wintypes import DWORD, LPCWSTR, UINT

import comtypes
from comtypes import COMMETHOD, GUID, IUnknown

# EDataFlow
E_RENDER = 0
E_CAPTURE = 1

# ERole
E_CONSOLE = 0
E_MULTIMEDIA = 1
E_COMMUNICATIONS = 2

DEVICE_STATE_ACTIVE = 0x1
STGM_READ = 0x0

CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
CLSID_PolicyConfigClient = GUID("{870AF99C-171D-4F9E-AF0D-E63DF40C2BC9}")

# PKEY_Device_FriendlyName -- "Speakers (Realtek HD Audio)", the string the
# Windows flyout shows. PKEY_DeviceInterface_FriendlyName (pid 2 on a different
# fmtid) gives the adapter name only, which is not what we want.
_PKEY_FMTID_DEVICE = GUID("{A45C254E-DF1C-4EFD-8020-67D146A850E0}")
_PKEY_PID_FRIENDLY_NAME = 14


class PROPERTYKEY(Structure):
    _fields_ = [("fmtid", GUID), ("pid", DWORD)]


class _PropVariantUnion(Union):
    _fields_ = [
        ("lVal", ctypes.c_long),
        ("uhVal", c_ulonglong),
        ("pwszVal", c_wchar_p),
        ("puuid", ctypes.c_void_p),
    ]


class PROPVARIANT(Structure):
    _fields_ = [
        ("vt", c_ushort),
        ("wReserved1", c_ushort),
        ("wReserved2", c_ushort),
        ("wReserved3", c_ushort),
        ("union", _PropVariantUnion),
    ]


VT_LPWSTR = 31


class IPropertyStore(IUnknown):
    _iid_ = GUID("{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}")
    _methods_ = [
        COMMETHOD([], comtypes.HRESULT, "GetCount",
                  (["out"], POINTER(DWORD), "cProps")),
        COMMETHOD([], comtypes.HRESULT, "GetAt",
                  (["in"], DWORD, "iProp"),
                  (["out"], POINTER(PROPERTYKEY), "pkey")),
        COMMETHOD([], comtypes.HRESULT, "GetValue",
                  (["in"], POINTER(PROPERTYKEY), "key"),
                  (["out"], POINTER(PROPVARIANT), "pv")),
        COMMETHOD([], comtypes.HRESULT, "SetValue",
                  (["in"], POINTER(PROPERTYKEY), "key"),
                  (["in"], POINTER(PROPVARIANT), "propvar")),
        COMMETHOD([], comtypes.HRESULT, "Commit"),
    ]


class IMMDevice(IUnknown):
    _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
    _methods_ = [
        COMMETHOD([], comtypes.HRESULT, "Activate",
                  (["in"], POINTER(GUID), "iid"),
                  (["in"], DWORD, "dwClsCtx"),
                  (["in"], POINTER(PROPVARIANT), "pActivationParams"),
                  (["out"], POINTER(POINTER(IUnknown)), "ppInterface")),
        COMMETHOD([], comtypes.HRESULT, "OpenPropertyStore",
                  (["in"], DWORD, "stgmAccess"),
                  (["out"], POINTER(POINTER(IPropertyStore)), "ppProperties")),
        COMMETHOD([], comtypes.HRESULT, "GetId",
                  (["out"], POINTER(c_wchar_p), "ppstrId")),
        COMMETHOD([], comtypes.HRESULT, "GetState",
                  (["out"], POINTER(DWORD), "pdwState")),
    ]


class IMMDeviceCollection(IUnknown):
    _iid_ = GUID("{0BD7A1BE-7A1A-44DB-8397-CC5392387B5E}")
    _methods_ = [
        COMMETHOD([], comtypes.HRESULT, "GetCount",
                  (["out"], POINTER(ctypes.c_uint), "pcDevices")),
        COMMETHOD([], comtypes.HRESULT, "Item",
                  (["in"], ctypes.c_uint, "nDevice"),
                  (["out"], POINTER(POINTER(IMMDevice)), "ppDevice")),
    ]


class IMMDeviceEnumerator(IUnknown):
    _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
    _methods_ = [
        COMMETHOD([], comtypes.HRESULT, "EnumAudioEndpoints",
                  (["in"], DWORD, "dataFlow"),
                  (["in"], DWORD, "dwStateMask"),
                  (["out"], POINTER(POINTER(IMMDeviceCollection)), "ppDevices")),
        COMMETHOD([], comtypes.HRESULT, "GetDefaultAudioEndpoint",
                  (["in"], DWORD, "dataFlow"),
                  (["in"], DWORD, "role"),
                  (["out"], POINTER(POINTER(IMMDevice)), "ppEndpoint")),
        COMMETHOD([], comtypes.HRESULT, "GetDevice",
                  (["in"], LPCWSTR, "pwstrId"),
                  (["out"], POINTER(POINTER(IMMDevice)), "ppDevice")),
        COMMETHOD([], comtypes.HRESULT, "RegisterEndpointNotificationCallback",
                  (["in"], POINTER(IUnknown), "pClient")),
        COMMETHOD([], comtypes.HRESULT, "UnregisterEndpointNotificationCallback",
                  (["in"], POINTER(IUnknown), "pClient")),
    ]


class IPolicyConfig(IUnknown):
    """Undocumented. Only SetDefaultEndpoint (vtable slot 13) is called.

    The preceding methods must still be declared, and in the right order, or
    the slot offset is wrong and we would call something else entirely. Their
    signatures are deliberately loose -- they exist to occupy the slots.
    """

    _iid_ = GUID("{F8679F50-850A-41CF-9C72-430F290290C8}")
    _methods_ = [
        COMMETHOD([], comtypes.HRESULT, "GetMixFormat",
                  (["in"], LPCWSTR, "d"), (["out"], POINTER(ctypes.c_void_p), "f")),
        COMMETHOD([], comtypes.HRESULT, "GetDeviceFormat",
                  (["in"], LPCWSTR, "d"), (["in"], ctypes.c_int, "b"),
                  (["out"], POINTER(ctypes.c_void_p), "f")),
        COMMETHOD([], comtypes.HRESULT, "ResetDeviceFormat",
                  (["in"], LPCWSTR, "d")),
        COMMETHOD([], comtypes.HRESULT, "SetDeviceFormat",
                  (["in"], LPCWSTR, "d"), (["in"], ctypes.c_void_p, "e"),
                  (["in"], ctypes.c_void_p, "m")),
        COMMETHOD([], comtypes.HRESULT, "GetProcessingPeriod",
                  (["in"], LPCWSTR, "d"), (["in"], ctypes.c_int, "b"),
                  (["out"], POINTER(ctypes.c_longlong), "p"),
                  (["out"], POINTER(ctypes.c_longlong), "m")),
        COMMETHOD([], comtypes.HRESULT, "SetProcessingPeriod",
                  (["in"], LPCWSTR, "d"), (["in"], ctypes.c_void_p, "p")),
        COMMETHOD([], comtypes.HRESULT, "GetShareMode",
                  (["in"], LPCWSTR, "d"), (["out"], POINTER(ctypes.c_void_p), "m")),
        COMMETHOD([], comtypes.HRESULT, "SetShareMode",
                  (["in"], LPCWSTR, "d"), (["in"], ctypes.c_void_p, "m")),
        COMMETHOD([], comtypes.HRESULT, "GetPropertyValue",
                  (["in"], LPCWSTR, "d"), (["in"], POINTER(PROPERTYKEY), "k"),
                  (["out"], POINTER(PROPVARIANT), "v")),
        COMMETHOD([], comtypes.HRESULT, "SetPropertyValue",
                  (["in"], LPCWSTR, "d"), (["in"], POINTER(PROPERTYKEY), "k"),
                  (["in"], POINTER(PROPVARIANT), "v")),
        COMMETHOD([], comtypes.HRESULT, "SetDefaultEndpoint",
                  (["in"], LPCWSTR, "deviceId"),
                  (["in"], DWORD, "role")),
        COMMETHOD([], comtypes.HRESULT, "SetEndpointVisibility",
                  (["in"], LPCWSTR, "d"), (["in"], ctypes.c_int, "visible")),
    ]


class IAudioEndpointVolume(IUnknown):
    """Only the master (all-channel) scalar volume is used here.

    Every method ahead of the ones we call must still be declared, in COM
    vtable order, or the slot offset is wrong -- same trick as IPolicyConfig
    above.
    """

    _iid_ = GUID("{5CDF2C82-841E-4546-9722-0CF74078229A}")
    _methods_ = [
        COMMETHOD([], comtypes.HRESULT, "RegisterControlChangeNotify",
                  (["in"], ctypes.c_void_p, "pNotify")),
        COMMETHOD([], comtypes.HRESULT, "UnregisterControlChangeNotify",
                  (["in"], ctypes.c_void_p, "pNotify")),
        COMMETHOD([], comtypes.HRESULT, "GetChannelCount",
                  (["out"], POINTER(UINT), "pnChannelCount")),
        COMMETHOD([], comtypes.HRESULT, "SetMasterVolumeLevel",
                  (["in"], ctypes.c_float, "fLevelDB"),
                  (["in"], POINTER(GUID), "pguidEventContext")),
        COMMETHOD([], comtypes.HRESULT, "SetMasterVolumeLevelScalar",
                  (["in"], ctypes.c_float, "fLevel"),
                  (["in"], POINTER(GUID), "pguidEventContext")),
        COMMETHOD([], comtypes.HRESULT, "GetMasterVolumeLevel",
                  (["out"], POINTER(ctypes.c_float), "pfLevelDB")),
        COMMETHOD([], comtypes.HRESULT, "GetMasterVolumeLevelScalar",
                  (["out"], POINTER(ctypes.c_float), "pfLevel")),
    ]


class Endpoint:
    """One playback device as Windows sees it."""

    def __init__(self, device_id, name, is_default=False):
        self.id = device_id
        self.name = name
        self.is_default = is_default

    def __repr__(self):
        return f"<Endpoint {self.name!r} default={self.is_default}>"


def _friendly_name(device):
    store = device.OpenPropertyStore(STGM_READ)
    key = PROPERTYKEY(_PKEY_FMTID_DEVICE, _PKEY_PID_FRIENDLY_NAME)
    value = store.GetValue(ctypes.byref(key))
    if value.vt != VT_LPWSTR or not value.union.pwszVal:
        return "(unnamed device)"
    return value.union.pwszVal


def _enumerator():
    return comtypes.CoCreateInstance(
        CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_ALL)


def list_outputs():
    """Active playback endpoints, default first-flagged. Never raises."""
    try:
        enum = _enumerator()
    except OSError:
        return []

    default_id = None
    try:
        default_id = enum.GetDefaultAudioEndpoint(E_RENDER, E_MULTIMEDIA).GetId()
    except (OSError, comtypes.COMError):
        pass  # no default set, or no audio hardware at all

    endpoints = []
    collection = enum.EnumAudioEndpoints(E_RENDER, DEVICE_STATE_ACTIVE)
    for i in range(collection.GetCount()):
        device = collection.Item(i)
        try:
            device_id = device.GetId()
            endpoints.append(
                Endpoint(device_id, _friendly_name(device), device_id == default_id))
        except (OSError, comtypes.COMError):
            continue  # endpoint vanished mid-enumeration; skip it
    return endpoints


def find_output(substring):
    """First endpoint whose name contains `substring`, case-insensitively."""
    needle = substring.lower()
    for endpoint in list_outputs():
        if needle in endpoint.name.lower():
            return endpoint
    return None


def set_default_output(device_id, roles=(E_CONSOLE, E_MULTIMEDIA, E_COMMUNICATIONS)):
    """Point the system default at `device_id`.

    Returns (ok, message). Setting all three roles matches what the Sound
    control panel does when you click "Set as default".
    """
    try:
        policy = comtypes.CoCreateInstance(
            CLSID_PolicyConfigClient, IPolicyConfig, comtypes.CLSCTX_ALL)
    except (OSError, comtypes.COMError) as exc:
        return False, f"IPolicyConfig unavailable on this build: {exc}"

    for role in roles:
        try:
            policy.SetDefaultEndpoint(device_id, role)
        except (OSError, comtypes.COMError) as exc:
            return False, f"SetDefaultEndpoint failed for role {role}: {exc}"
    return True, "default output updated"


def _endpoint_volume(device_id):
    enumerator = _enumerator()
    device = enumerator.GetDevice(device_id)
    raw = device.Activate(IAudioEndpointVolume._iid_, comtypes.CLSCTX_ALL, None)
    return raw.QueryInterface(IAudioEndpointVolume)


def get_volume(device_id):
    """Current master volume for one endpoint, 0.0-1.0, or None if unavailable."""
    try:
        return _endpoint_volume(device_id).GetMasterVolumeLevelScalar()
    except (OSError, comtypes.COMError):
        return None


def set_volume(device_id, level):
    """Set one endpoint's master volume, 0.0-1.0. Returns (ok, note)."""
    try:
        _endpoint_volume(device_id).SetMasterVolumeLevelScalar(
            max(0.0, min(1.0, level)), None)
        return True, "volume set"
    except (OSError, comtypes.COMError) as exc:
        return False, f"could not set volume: {exc}"


def current_default():
    for endpoint in list_outputs():
        if endpoint.is_default:
            return endpoint
    return None
