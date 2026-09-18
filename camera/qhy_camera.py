"""Thin wrapper around qhyccd.dll — duck-types camera.asi_camera.AsiCamera."""

from __future__ import annotations

import os
import sys
import threading
import time
import ctypes
from ctypes import (
    POINTER,
    byref,
    c_bool,
    c_char_p,
    c_double,
    c_uint8,
    c_uint32,
    c_void_p,
    create_string_buffer,
)
from pathlib import Path

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parent.parent

QHYCCD_SUCCESS = 0
QHYCCD_ERROR_NO_DEVICE = -2

CONTROL_GAIN = 6
CONTROL_EXPOSURE = 8
CONTROL_TRANSFERBIT = 10
CAM_IS_COLOR = 59

QHYCCD_LIVE_MODE = 0x01

# qhyccdstruct.h BAYER_ID -> SER ColourID (same mapping as asi_camera.py)
_BAYER_TO_SER_COLOUR_ID = {
    1: 10,  # BAYER_GB -> GBRG
    2: 9,   # BAYER_GR -> GRBG
    3: 11,  # BAYER_BG -> BGGR
    4: 8,   # BAYER_RG -> RGGB
}

_DEFAULT_SDK_PATHS: list[Path] = [
    _REPO_ROOT / "sdk" / "qhy" / "x64" / "qhyccd.dll",
    _REPO_ROOT / "sdk" / "qhy" / "linux" / "libqhyccd.so",
    Path("/usr/local/lib/libqhyccd.so"),
    Path("/usr/lib/libqhyccd.so"),
    Path("/usr/lib/x86_64-linux-gnu/libqhyccd.so"),
]

_sdk_lock = threading.Lock()
_sdk_refcount = 0
_sdk_dll: object | None = None
_sdk_dll_dir: Path | None = None


def _signed32(value: int) -> int:
    if value >= 0x80000000:
        return value - 0x100000000
    return value


def find_sdk_library(sdk_path: str | None = None) -> str:
    if sdk_path:
        path = Path(sdk_path)
        if not path.exists():
            raise RuntimeError(f"QHY SDK not found at {sdk_path!r}")
        return str(path)
    for candidate in _DEFAULT_SDK_PATHS:
        if candidate.exists():
            return str(candidate)
    raise RuntimeError(
        "qhyccd library not found. Pass sdk_path explicitly or install the QHY SDK "
        "(bundled copy expected at sdk/qhy/x64/qhyccd.dll on Windows)."
    )


def _load_dll(library_path: str):
    global _sdk_dll, _sdk_dll_dir
    if _sdk_dll is not None:
        return _sdk_dll
    path = Path(library_path)
    _sdk_dll_dir = path.parent
    if sys.platform == "win32":
        os.add_dll_directory(str(_sdk_dll_dir))
        dll = ctypes.WinDLL(str(path))
    else:
        dll = ctypes.CDLL(str(path))
    _bind_dll(dll)
    _sdk_dll = dll
    return dll


def _bind_dll(dll) -> None:
    dll.InitQHYCCDResource.argtypes = []
    dll.InitQHYCCDResource.restype = c_uint32

    dll.ReleaseQHYCCDResource.argtypes = []
    dll.ReleaseQHYCCDResource.restype = c_uint32

    dll.ScanQHYCCD.argtypes = []
    dll.ScanQHYCCD.restype = c_uint32

    dll.GetQHYCCDId.argtypes = [c_uint32, c_char_p]
    dll.GetQHYCCDId.restype = c_uint32

    dll.GetQHYCCDModel.argtypes = [c_char_p, c_char_p]
    dll.GetQHYCCDModel.restype = c_uint32

    dll.OpenQHYCCD.argtypes = [c_char_p]
    dll.OpenQHYCCD.restype = c_void_p

    dll.CloseQHYCCD.argtypes = [c_void_p]
    dll.CloseQHYCCD.restype = c_uint32

    dll.InitQHYCCD.argtypes = [c_void_p]
    dll.InitQHYCCD.restype = c_uint32

    dll.SetQHYCCDStreamMode.argtypes = [c_void_p, c_uint8]
    dll.SetQHYCCDStreamMode.restype = c_uint32

    dll.SetQHYCCDDebayerOnOff.argtypes = [c_void_p, c_bool]
    dll.SetQHYCCDDebayerOnOff.restype = c_uint32

    dll.GetQHYCCDEffectiveArea.argtypes = [
        c_void_p, POINTER(c_uint32), POINTER(c_uint32), POINTER(c_uint32), POINTER(c_uint32),
    ]
    dll.GetQHYCCDEffectiveArea.restype = c_uint32

    dll.SetQHYCCDResolution.argtypes = [c_void_p, c_uint32, c_uint32, c_uint32, c_uint32]
    dll.SetQHYCCDResolution.restype = c_uint32

    dll.GetQHYCCDMemLength.argtypes = [c_void_p]
    dll.GetQHYCCDMemLength.restype = c_uint32

    dll.BeginQHYCCDLive.argtypes = [c_void_p]
    dll.BeginQHYCCDLive.restype = c_uint32

    dll.GetQHYCCDLiveFrame.argtypes = [
        c_void_p,
        POINTER(c_uint32),
        POINTER(c_uint32),
        POINTER(c_uint32),
        POINTER(c_uint32),
        POINTER(c_uint8),
    ]
    dll.GetQHYCCDLiveFrame.restype = c_uint32

    dll.StopQHYCCDLive.argtypes = [c_void_p]
    dll.StopQHYCCDLive.restype = c_uint32

    dll.IsQHYCCDControlAvailable.argtypes = [c_void_p, c_uint32]
    dll.IsQHYCCDControlAvailable.restype = c_uint32

    dll.GetQHYCCDParamMinMaxStep.argtypes = [
        c_void_p, c_uint32, POINTER(c_double), POINTER(c_double), POINTER(c_double),
    ]
    dll.GetQHYCCDParamMinMaxStep.restype = c_uint32

    dll.GetQHYCCDParam.argtypes = [c_void_p, c_uint32]
    dll.GetQHYCCDParam.restype = c_double

    dll.SetQHYCCDParam.argtypes = [c_void_p, c_uint32, c_double]
    dll.SetQHYCCDParam.restype = c_uint32


def _sdk_acquire(library_path: str) -> None:
    global _sdk_refcount
    with _sdk_lock:
        dll = _load_dll(library_path)
        if _sdk_refcount == 0:
            ret = dll.InitQHYCCDResource()
            if _signed32(ret) != QHYCCD_SUCCESS:
                raise RuntimeError(f"InitQHYCCDResource failed (code {ret})")
        _sdk_refcount += 1


def _sdk_release() -> None:
    global _sdk_refcount, _sdk_dll, _sdk_dll_dir
    with _sdk_lock:
        if _sdk_refcount == 0:
            return
        _sdk_refcount -= 1
        if _sdk_refcount == 0 and _sdk_dll is not None:
            _sdk_dll.ReleaseQHYCCDResource()
            _sdk_dll = None
            _sdk_dll_dir = None


def _check(ret: int, what: str) -> None:
    if _signed32(ret) == QHYCCD_SUCCESS:
        return
    raise RuntimeError(f"QHY SDK {what} failed (code {ret})")


class QhyCamera:
    """A real QHY camera via qhyccd.dll — interface matches AsiCamera / MockAsiCamera."""

    def __init__(self, camera_id: int = 0, sdk_path: str | None = None, bit_depth: int = 8):
        self._camera_id = camera_id
        self._sdk_path = sdk_path
        self._handle: c_void_p | None = None
        self._dll = None
        self._camera_id_str: bytes | None = None
        self._width = 0
        self._height = 0
        self._roi_x = 0
        self._roi_y = 0
        self._is_color = False
        self._bayer_id = 4  # BAYER_RG — QHY533C and most QHY OSC bodies
        self._bit_depth = bit_depth
        self._streaming = False
        self._frame_buffer: np.ndarray | None = None
        self._sdk_held = False

    def open(self) -> None:
        library_path = find_sdk_library(self._sdk_path)
        _sdk_acquire(library_path)
        self._sdk_held = True
        self._dll = _load_dll(library_path)
        try:
            count_raw = self._dll.ScanQHYCCD()
            count = _signed32(count_raw)
            if count == QHYCCD_ERROR_NO_DEVICE or count <= 0:
                raise RuntimeError("no QHY camera detected")
            if self._camera_id >= count:
                raise RuntimeError(
                    f"QHY camera index {self._camera_id} out of range ({count} camera(s) found)"
                )
            id_buf = create_string_buffer(256)
            _check(self._dll.GetQHYCCDId(self._camera_id, id_buf), "GetQHYCCDId")
            self._camera_id_str = id_buf.value
            handle = self._dll.OpenQHYCCD(self._camera_id_str)
            if not handle:
                raise RuntimeError("OpenQHYCCD failed")
            self._handle = handle
            _check(self._dll.InitQHYCCD(self._handle), "InitQHYCCD")
            _check(self._dll.SetQHYCCDStreamMode(self._handle, QHYCCD_LIVE_MODE), "SetQHYCCDStreamMode")
            _check(self._dll.SetQHYCCDDebayerOnOff(self._handle, False), "SetQHYCCDDebayerOnOff")
            self._is_color = self._detect_is_color()
            start_x = c_uint32()
            start_y = c_uint32()
            size_x = c_uint32()
            size_y = c_uint32()
            _check(
                self._dll.GetQHYCCDEffectiveArea(
                    self._handle, byref(start_x), byref(start_y), byref(size_x), byref(size_y),
                ),
                "GetQHYCCDEffectiveArea",
            )
            self._roi_x = start_x.value
            self._roi_y = start_y.value
            _check(
                self._dll.SetQHYCCDResolution(
                    self._handle, start_x.value, start_y.value, size_x.value, size_y.value,
                ),
                "SetQHYCCDResolution",
            )
            self._width = size_x.value
            self._height = size_y.value
            buf_len = self._dll.GetQHYCCDMemLength(self._handle)
            self._frame_buffer = np.empty(buf_len, dtype=np.uint8)
            self._apply_bit_depth(self._bit_depth)
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if self._handle is not None and self._dll is not None:
            if self._streaming:
                try:
                    self._dll.StopQHYCCDLive(self._handle)
                except Exception:  # noqa: BLE001
                    pass
                self._streaming = False
            try:
                self._dll.CloseQHYCCD(self._handle)
            except Exception:  # noqa: BLE001
                pass
        self._handle = None
        self._dll = None
        self._frame_buffer = None
        if self._sdk_held:
            _sdk_release()
            self._sdk_held = False

    def _detect_is_color(self) -> bool:
        assert self._handle is not None and self._dll is not None
        if self._dll.IsQHYCCDControlAvailable(self._handle, CAM_IS_COLOR) == QHYCCD_SUCCESS:
            return True
        if self._camera_id_str:
            model_buf = create_string_buffer(256)
            if self._dll.GetQHYCCDModel(self._camera_id_str, model_buf) == QHYCCD_SUCCESS:
                model = model_buf.value.decode(errors="replace").upper()
                if model.endswith("C"):
                    return True
                if model.endswith("M"):
                    return False
        return False

    def set_roi(self, x: int, y: int, width: int, height: int) -> None:
        assert self._handle is not None and self._dll is not None
        width = max(8, (width // 8) * 8)
        height = max(2, (height // 2) * 2)
        was_streaming = self._streaming
        if was_streaming:
            _check(self._dll.StopQHYCCDLive(self._handle), "StopQHYCCDLive")
            self._streaming = False
        try:
            _check(
                self._dll.SetQHYCCDResolution(self._handle, x, y, width, height),
                "SetQHYCCDResolution",
            )
            self._roi_x, self._roi_y = x, y
            self._width, self._height = width, height
        finally:
            if was_streaming:
                _check(self._dll.BeginQHYCCDLive(self._handle), "BeginQHYCCDLive")
                self._streaming = True

    def _apply_bit_depth(self, bit_depth: int) -> None:
        assert self._handle is not None and self._dll is not None
        if bit_depth not in (8, 16):
            raise ValueError(f"unsupported bit depth {bit_depth!r} (must be 8 or 16)")
        if self._dll.IsQHYCCDControlAvailable(self._handle, CONTROL_TRANSFERBIT) != QHYCCD_SUCCESS:
            if bit_depth != 8:
                raise ValueError("this QHY camera does not support 16-bit output")
            return
        _check(self._dll.SetQHYCCDParam(self._handle, CONTROL_TRANSFERBIT, float(bit_depth)), "SetQHYCCDParam TRANSFERBIT")
        self._bit_depth = bit_depth

    def set_bit_depth(self, bit_depth: int) -> None:
        assert self._handle is not None and self._dll is not None
        was_streaming = self._streaming
        if was_streaming:
            _check(self._dll.StopQHYCCDLive(self._handle), "StopQHYCCDLive")
            self._streaming = False
        try:
            self._apply_bit_depth(bit_depth)
        finally:
            if was_streaming:
                _check(self._dll.BeginQHYCCDLive(self._handle), "BeginQHYCCDLive")
                self._streaming = True

    def set_exposure_us(self, microseconds: int) -> None:
        assert self._handle is not None and self._dll is not None
        _check(
            self._dll.SetQHYCCDParam(self._handle, CONTROL_EXPOSURE, float(microseconds)),
            "SetQHYCCDParam EXPOSURE",
        )

    def set_gain(self, gain: int) -> None:
        assert self._handle is not None and self._dll is not None
        _check(self._dll.SetQHYCCDParam(self._handle, CONTROL_GAIN, float(gain)), "SetQHYCCDParam GAIN")

    def get_controls(self) -> dict:
        assert self._handle is not None and self._dll is not None
        controls: dict = {}
        for key, control_id in (("Exposure", CONTROL_EXPOSURE), ("Gain", CONTROL_GAIN)):
            if self._dll.IsQHYCCDControlAvailable(self._handle, control_id) != QHYCCD_SUCCESS:
                continue
            min_v = c_double()
            max_v = c_double()
            step_v = c_double()
            _check(
                self._dll.GetQHYCCDParamMinMaxStep(
                    self._handle, control_id, byref(min_v), byref(max_v), byref(step_v),
                ),
                f"GetQHYCCDParamMinMaxStep {key}",
            )
            default_v = self._dll.GetQHYCCDParam(self._handle, control_id)
            controls[key] = {
                "Name": key,
                "MinValue": int(min_v.value),
                "MaxValue": int(max_v.value),
                "DefaultValue": int(default_v),
            }
        return controls

    def start_streaming(self) -> None:
        assert self._handle is not None and self._dll is not None
        _check(self._dll.BeginQHYCCDLive(self._handle), "BeginQHYCCDLive")
        self._streaming = True

    def stop_streaming(self) -> None:
        assert self._handle is not None and self._dll is not None
        _check(self._dll.StopQHYCCDLive(self._handle), "StopQHYCCDLive")
        self._streaming = False

    def read_frame(self, timeout_ms: int = 2000) -> np.ndarray:
        assert self._handle is not None and self._dll is not None and self._frame_buffer is not None
        deadline = time.monotonic() + timeout_ms / 1000.0
        w = c_uint32()
        h = c_uint32()
        bpp = c_uint32()
        channels = c_uint32()
        buf_ptr = self._frame_buffer.ctypes.data_as(POINTER(c_uint8))
        while time.monotonic() < deadline:
            ret = self._dll.GetQHYCCDLiveFrame(
                self._handle, byref(w), byref(h), byref(bpp), byref(channels), buf_ptr,
            )
            if _signed32(ret) == QHYCCD_SUCCESS:
                break
            time.sleep(0.002)
        else:
            raise TimeoutError("GetQHYCCDLiveFrame timed out")
        width, height = w.value, h.value
        if width == 0 or height == 0:
            raise RuntimeError("GetQHYCCDLiveFrame returned empty dimensions")
        if bpp.value == 16:
            pixels = np.frombuffer(self._frame_buffer.data, dtype=np.uint16, count=width * height)
            return pixels.reshape(height, width).copy()
        count = width * height * max(channels.value, 1)
        raw = self._frame_buffer[:count]
        if channels.value >= 3:
            rgb = raw.reshape(height, width, channels.value)
            # Debayer is off; if the SDK still delivers RGB, take green as a mono fallback.
            return rgb[:, :, 1].copy()
        return raw.reshape(height, width).copy()

    def get_dropped_frames(self) -> int:
        return 0

    def bayer_pattern_ser_colour_id(self) -> int:
        if not self._is_color:
            return 0
        return _BAYER_TO_SER_COLOUR_ID.get(self._bayer_id, 8)

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def is_color(self) -> bool:
        return self._is_color

    @property
    def bit_depth(self) -> int:
        return self._bit_depth
