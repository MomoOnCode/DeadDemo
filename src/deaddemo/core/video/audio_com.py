"""comtypes declarations for WASAPI process loopback. Import this module ONLY from the capture thread.

comtypes calls ``CoInitializeEx`` on the importing thread the first time it is imported; we ask for the
multithreaded apartment via ``sys.coinit_flags`` before that happens. Importing it on the GUI thread
(already an STA) would raise ``RPC_E_CHANGED_MODE``.

Facts this relies on (Windows SDK 10.0.28000 headers, MS ApplicationLoopback sample):
* the virtual device ``VAD\\Process_Loopback`` is activated with ``ActivateAudioInterfaceAsync`` and a
  VT_BLOB ``PROPVARIANT`` holding ``AUDIOCLIENT_ACTIVATION_PARAMS``;
* the completion handler must implement ``IActivateAudioInterfaceCompletionHandler`` *and* the marker
  ``IAgileObject``, or the call fails with ``E_ILLEGAL_METHOD_CALL``;
* ``GetMixFormat`` is E_NOTIMPL on this device: the caller fixes the format and initializes with
  LOOPBACK | EVENTCALLBACK | AUTOCONVERTPCM in shared mode.
"""

from __future__ import annotations

import ctypes
import sys
import threading
from collections.abc import Iterator
from ctypes import HRESULT, POINTER, byref, c_int, c_longlong, c_uint, c_ulong, c_ulonglong, c_void_p, wintypes

if "comtypes" not in sys.modules:
    sys.coinit_flags = 0  # COINIT_MULTITHREADED for the thread that imports comtypes (this one)

import comtypes  # noqa: E402
from comtypes import COMMETHOD, GUID, STDMETHOD, COMObject, IUnknown  # noqa: E402

from deaddemo.core.video.audio import (  # noqa: E402
    AUDCLNT_E_UNSUPPORTED_FORMAT,
    AUDCLNT_SHAREMODE_SHARED,
    AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM,
    AUDCLNT_STREAMFLAGS_EVENTCALLBACK,
    AUDCLNT_STREAMFLAGS_LOOPBACK,
    PROPVARIANT,
    VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK,
    WAVEFORMATEX,
    AudioCaptureError,
    activation_params,
    wave_format,
)

COINIT_MULTITHREADED = 0
BYTE = ctypes.c_ubyte


class IAgileObject(IUnknown):
    _iid_ = GUID("{94ea2b94-e9cc-49e0-c0ff-ee64ca8f5b90}")
    _methods_: list = []


class IActivateAudioInterfaceAsyncOperation(IUnknown):
    _iid_ = GUID("{72A22D78-CDE4-431D-B8CC-843A71199B6D}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetActivateResult", (["out"], POINTER(HRESULT), "activateResult"),
                  (["out"], POINTER(POINTER(IUnknown)), "activatedInterface")),
    ]


class IActivateAudioInterfaceCompletionHandler(IUnknown):
    _iid_ = GUID("{41D949AB-9862-444A-80F6-C261334DA5EB}")
    _methods_ = [STDMETHOD(HRESULT, "ActivateCompleted", [POINTER(IActivateAudioInterfaceAsyncOperation)])]


class IAudioClient(IUnknown):
    _iid_ = GUID("{1CB9AD4C-DBFA-4c32-B178-C2F568A703B2}")
    _methods_ = [
        COMMETHOD([], HRESULT, "Initialize", (["in"], c_int, "ShareMode"), (["in"], c_ulong, "StreamFlags"),
                  (["in"], c_longlong, "hnsBufferDuration"), (["in"], c_longlong, "hnsPeriodicity"),
                  (["in"], POINTER(WAVEFORMATEX), "pFormat"), (["in"], POINTER(GUID), "AudioSessionGuid")),
        COMMETHOD([], HRESULT, "GetBufferSize", (["out"], POINTER(c_uint), "pNumBufferFrames")),
        COMMETHOD([], HRESULT, "GetStreamLatency", (["out"], POINTER(c_longlong), "phnsLatency")),
        COMMETHOD([], HRESULT, "GetCurrentPadding", (["out"], POINTER(c_uint), "pNumPaddingFrames")),
        COMMETHOD([], HRESULT, "IsFormatSupported", (["in"], c_int, "ShareMode"),
                  (["in"], POINTER(WAVEFORMATEX), "pFormat"),
                  (["out"], POINTER(POINTER(WAVEFORMATEX)), "ppClosestMatch")),
        COMMETHOD([], HRESULT, "GetMixFormat", (["out"], POINTER(POINTER(WAVEFORMATEX)), "ppDeviceFormat")),
        COMMETHOD([], HRESULT, "GetDevicePeriod", (["out"], POINTER(c_longlong), "phnsDefaultDevicePeriod"),
                  (["out"], POINTER(c_longlong), "phnsMinimumDevicePeriod")),
        COMMETHOD([], HRESULT, "Start"),
        COMMETHOD([], HRESULT, "Stop"),
        COMMETHOD([], HRESULT, "Reset"),
        COMMETHOD([], HRESULT, "SetEventHandle", (["in"], wintypes.HANDLE, "eventHandle")),
        COMMETHOD([], HRESULT, "GetService", (["in"], POINTER(GUID), "riid"), (["out"], POINTER(c_void_p), "ppv")),
    ]


class IAudioCaptureClient(IUnknown):
    _iid_ = GUID("{C8ADBD64-E71E-48a0-A4DE-185C395CD317}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetBuffer", (["out"], POINTER(POINTER(BYTE)), "ppData"),
                  (["out"], POINTER(c_uint), "pNumFramesToRead"), (["out"], POINTER(c_ulong), "pdwFlags"),
                  (["out"], POINTER(c_ulonglong), "pu64DevicePosition"),
                  (["out"], POINTER(c_ulonglong), "pu64QPCPosition")),
        COMMETHOD([], HRESULT, "ReleaseBuffer", (["in"], c_uint, "NumFramesRead")),
        COMMETHOD([], HRESULT, "GetNextPacketSize", (["out"], POINTER(c_uint), "pNumFramesInNextPacket")),
    ]


class _CompletionHandler(COMObject):
    _com_interfaces_ = [IActivateAudioInterfaceCompletionHandler, IAgileObject]

    def __init__(self) -> None:
        super().__init__()
        self.done = threading.Event()
        self.hr: int | None = None
        self.client: IAudioClient | None = None
        self.exc: Exception | None = None

    def ActivateCompleted(self, this, operation):  # noqa: N802 - COM method name
        try:
            hr, punk = operation.GetActivateResult()
            self.hr = int(hr)
            if self.hr >= 0 and punk:
                self.client = punk.QueryInterface(IAudioClient)
        except Exception as exc:  # noqa: BLE001 - never let an exception escape a COM callback
            self.exc = exc
        finally:
            self.done.set()
        return 0  # S_OK


_mmdevapi = ctypes.WinDLL("mmdevapi")
_activate = _mmdevapi.ActivateAudioInterfaceAsync
_activate.argtypes = [wintypes.LPCWSTR, POINTER(GUID), POINTER(PROPVARIANT),
                      POINTER(IActivateAudioInterfaceCompletionHandler),
                      POINTER(POINTER(IActivateAudioInterfaceAsyncOperation))]
_activate.restype = HRESULT

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateEventW.restype = wintypes.HANDLE
_kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def initialize_thread() -> None:
    try:
        comtypes.CoInitializeEx(COINIT_MULTITHREADED)
    except OSError as exc:  # RPC_E_CHANGED_MODE: this thread is already an STA
        raise AudioCaptureError(f"COM apartment mismatch on the capture thread: {exc}") from exc


def uninitialize_thread() -> None:
    try:
        comtypes.CoUninitialize()
    except OSError:
        pass


def activate_process_loopback(pid: int, timeout: float = 5.0) -> IAudioClient:
    params, pv = activation_params(pid)
    handler = _CompletionHandler()
    op = POINTER(IActivateAudioInterfaceAsyncOperation)()
    handler_ptr = handler.QueryInterface(IActivateAudioInterfaceCompletionHandler)
    _activate(VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK, byref(IAudioClient._iid_), byref(pv), handler_ptr, byref(op))
    if not handler.done.wait(timeout):
        raise AudioCaptureError("audio activation timed out")
    if handler.exc is not None:
        raise AudioCaptureError(f"audio activation callback failed: {handler.exc}")
    if handler.client is None:
        raise AudioCaptureError(f"audio activation failed: hr=0x{(handler.hr or 0) & 0xFFFFFFFF:08x}")
    del params  # keep the blob alive until here
    return handler.client


class LoopbackStream:
    """One initialized and started process-loopback capture; use it from the thread that opened it."""

    def __init__(self, pid: int, sample_rate: int, channels: int):
        self.pid = pid
        self.sample_rate = sample_rate
        self.channels = channels
        self.client: IAudioClient | None = None
        self.capture: IAudioCaptureClient | None = None
        self.event: int | None = None

    def open(self, timeout: float = 5.0) -> None:
        client = activate_process_loopback(self.pid, timeout)
        flags = AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_EVENTCALLBACK | AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM
        try:
            fmt = wave_format(self.sample_rate, self.channels)
            client.Initialize(AUDCLNT_SHAREMODE_SHARED, flags, 0, 0, byref(fmt), None)
        except comtypes.COMError as exc:
            if (exc.hresult & 0xFFFFFFFF) != AUDCLNT_E_UNSUPPORTED_FORMAT:
                raise AudioCaptureError(f"IAudioClient.Initialize failed: {exc}") from exc
            self.sample_rate = 44100  # the MS sample's format; try once more
            fmt = wave_format(self.sample_rate, self.channels)
            client.Initialize(AUDCLNT_SHAREMODE_SHARED, flags, 0, 0, byref(fmt), None)
        self._fmt = fmt  # keep alive while the client may reference it
        self.event = _kernel32.CreateEventW(None, False, False, None)
        if not self.event:
            raise AudioCaptureError("CreateEvent failed")
        client.SetEventHandle(self.event)
        raw = client.GetService(byref(IAudioCaptureClient._iid_))
        if not raw:
            raise AudioCaptureError("GetService(IAudioCaptureClient) returned null")
        self.capture = ctypes.cast(raw, POINTER(IAudioCaptureClient))  # takes over GetService's reference
        client.Start()
        self.client = client

    def wait(self, timeout_ms: int) -> bool:
        return _kernel32.WaitForSingleObject(self.event, timeout_ms) == 0

    def packets(self) -> Iterator[tuple[bytes, int, int]]:
        assert self.capture is not None
        frame_bytes = self.channels * 2
        while True:
            n = self.capture.GetNextPacketSize()
            if not n:
                return
            data_ptr, frames, flags, _dev_pos, _qpc = self.capture.GetBuffer()
            try:
                data = ctypes.string_at(data_ptr, frames * frame_bytes) if frames and data_ptr else b""
            finally:
                self.capture.ReleaseBuffer(frames)
            yield data, int(frames), int(flags)

    def close(self) -> None:
        if self.client is not None:
            try:
                self.client.Stop()
            except comtypes.COMError:
                pass
        self.capture = None
        self.client = None
        if self.event:
            _kernel32.CloseHandle(self.event)
            self.event = None
