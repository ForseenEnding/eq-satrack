"""Thin wrapper around SVBCameraSDK.dll — duck-types camera.asi_camera.AsiCamera."""

from __future__ import annotations

import os
import sys
import threading
import time
import ctypes
from ctypes import (
    POINTER,
    byref,
    c_char,
    c_int,
    c_int32,
    c_long,
    c_uint8,
    c_uint32,
    Structure,
)
from pathlib import Path

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parent.parent

SVB_SUCCESS = 0
SVB_ERROR_INVALID_INDEX = 1

SVB_GAIN = 0
SVB_EXPOSURE = 1

SVB_IMG_RAW8 = 0
SVB_IMG_RAW16 = 5
SVB_MODE_NORMAL = 0

SVB_FALSE = 0
SVB_TRUE = 1

# SVB_BAYER_* -> SER ColourID (same mapping as asi_camera.py / qhy_camera.py)
_BAYER_TO_SER_COLOUR_ID = {
    0: 8,   # SVB_BAYER_RG -> RGGB
    1: 11,  # SVB_BAYER_BG -> BGGR
    2: 9,   # SVB_BAYER_GR -> GRBG
    3: 10,  # SVB_BAYER_GB -> GBRG
}

_DEFAULT_SDK_PATHS: list[Path] = [
    _REPO_ROOT / "sdk" / "svbony" / "lib" / "x64" / "SVBCameraSDK.dll",
    _REPO_ROOT / "sdk" / "svbony" / "lib" / "x86" / "SVBCameraSDK.dll",
    Path("/usr/local/lib/libSVBCameraSDK.so"),
    Path("/usr/lib/libSVBCameraSDK.so"),
    Path("/usr/lib/x86_64-linux-gnu/libSVBCameraSDK.so"),
]

_sdk_lock = threading.Lock()
_sdk_dll: object | None = None
_sdk_dll_dir: Path | None = None


class _SVB_CAMERA_INFO(Structure):
    _fields_ = [
        ("FriendlyName", c_char * 32),
        ("CameraSN", c_char * 32),
        ("PortType", c_char * 32),
        ("DeviceID", c_uint32),
        ("CameraID", c_int32),
    ]


class _SVB_CAMERA_PROPERTY(Structure):
    _fields_ = [
        ("MaxHeight", c_long),
        ("MaxWidth", c_long),
        ("IsColorCam", c_int32),
        ("BayerPattern", c_int32),
        ("SupportedBins", c_int32 * 16),
        ("SupportedVideoFormat", c_int32 * 8),
        ("MaxBitDepth", c_int32),
        ("IsTriggerCam", c_int32),
    ]


class _SVB_CONTROL_CAPS(Structure):
    _fields_ = [
        ("Name", c_char * 64),
        ("Description", c_char * 128),
        ("MaxValue", c_long),
        ("MinValue", c_long),
        ("DefaultValue", c_long),
        ("IsAutoSupported", c_int32),
        ("IsWritable", c_int32),
        ("ControlType", c_int32),
        ("Unused", c_char * 32),
    ]


def find_sdk_library(sdk_path: str | None = None) -> str:
    if sdk_path:
        path = Path(sdk_path)
        if not path.exists():
            raise RuntimeError(f"SVBONY SDK not found at {sdk_path!r}")
        return str(path)
    for candidate in _DEFAULT_SDK_PATHS:
        if candidate.exists():
            return str(candidate)
    raise RuntimeError(
        "SVBCameraSDK library not found. Pass sdk_path explicitly or add the SDK "
        "(bundled copy expected at sdk/svbony/lib/x64/SVBCameraSDK.dll on Windows)."
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
    dll.SVBGetNumOfConnectedCameras.argtypes = []
    dll.SVBGetNumOfConnectedCameras.restype = c_int

    dll.SVBGetCameraInfo.argtypes = [POINTER(_SVB_CAMERA_INFO), c_int]
    dll.SVBGetCameraInfo.restype = c_int

    dll.SVBOpenCamera.argtypes = [c_int]
    dll.SVBOpenCamera.restype = c_int

    dll.SVBCloseCamera.argtypes = [c_int]
    dll.SVBCloseCamera.restype = c_int

    dll.SVBGetCameraProperty.argtypes = [c_int, POINTER(_SVB_CAMERA_PROPERTY)]
    dll.SVBGetCameraProperty.restype = c_int

    dll.SVBGetNumOfControls.argtypes = [c_int, POINTER(c_int)]
    dll.SVBGetNumOfControls.restype = c_int

    dll.SVBGetControlCaps.argtypes = [c_int, c_int, POINTER(_SVB_CONTROL_CAPS)]
    dll.SVBGetControlCaps.restype = c_int

    dll.SVBGetControlValue.argtypes = [c_int, c_int, POINTER(c_long), POINTER(c_int32)]
    dll.SVBGetControlValue.restype = c_int

    dll.SVBSetControlValue.argtypes = [c_int, c_int, c_long, c_int32]
    dll.SVBSetControlValue.restype = c_int

    dll.SVBSetOutputImageType.argtypes = [c_int, c_int]
    dll.SVBSetOutputImageType.restype = c_int

    dll.SVBSetROIFormat.argtypes = [c_int, c_int, c_int, c_int, c_int, c_int]
    dll.SVBSetROIFormat.restype = c_int

    dll.SVBSetCameraMode.argtypes = [c_int, c_int]
    dll.SVBSetCameraMode.restype = c_int

    dll.SVBStartVideoCapture.argtypes = [c_int]
    dll.SVBStartVideoCapture.restype = c_int

    dll.SVBStopVideoCapture.argtypes = [c_int]
    dll.SVBStopVideoCapture.restype = c_int

    dll.SVBGetVideoData.argtypes = [c_int, POINTER(c_uint8), c_long, c_int]
    dll.SVBGetVideoData.restype = c_int

    dll.SVBGetDroppedFrames.argtypes = [c_int, POINTER(c_int)]
    dll.SVBGetDroppedFrames.restype = c_int


def _check(ret: int, what: str) -> None:
    if ret == SVB_SUCCESS:
        return
    raise RuntimeError(f"SVBONY SDK {what} failed (code {ret})")


class SvbonyCamera:
    """A real SVBONY camera via SVBCameraSDK — interface matches AsiCamera / MockAsiCamera."""

    def __init__(self, camera_id: int = 0, sdk_path: str | None = None, bit_depth: int = 8):
        # camera_id is the index into SVBGetCameraInfo (0 = first connected camera).
        self._camera_index = camera_id
        self._sdk_path = sdk_path
        self._svb_camera_id: int | None = None
        self._dll = None
        self._width = 0
        self._height = 0
        self._is_color = False
        self._bayer_pattern = 0
        self._max_bit_depth = 8
        self._bit_depth = bit_depth
        self._streaming = False
        self._frame_buffer: np.ndarray | None = None
        self._control_caps: dict[str, _SVB_CONTROL_CAPS] = {}

    def open(self) -> None:
        library_path = find_sdk_library(self._sdk_path)
        self._dll = _load_dll(library_path)
        count = self._dll.SVBGetNumOfConnectedCameras()
        if count <= 0:
            raise RuntimeError("no SVBONY camera detected")
        if self._camera_index >= count:
            raise RuntimeError(
                f"SVBONY camera index {self._camera_index} out of range ({count} camera(s) found)"
            )
        info = _SVB_CAMERA_INFO()
        _check(self._dll.SVBGetCameraInfo(byref(info), self._camera_index), "SVBGetCameraInfo")
        self._svb_camera_id = info.CameraID
        _check(self._dll.SVBOpenCamera(self._svb_camera_id), "SVBOpenCamera")
        try:
            props = _SVB_CAMERA_PROPERTY()
            _check(self._dll.SVBGetCameraProperty(self._svb_camera_id, byref(props)), "SVBGetCameraProperty")
            self._width = int(props.MaxWidth)
            self._height = int(props.MaxHeight)
            self._is_color = bool(props.IsColorCam)
            self._bayer_pattern = int(props.BayerPattern)
            self._max_bit_depth = int(props.MaxBitDepth)
            self._load_control_caps()
            _check(self._dll.SVBSetCameraMode(self._svb_camera_id, SVB_MODE_NORMAL), "SVBSetCameraMode")
            self._apply_bit_depth(self._bit_depth)
            _check(
                self._dll.SVBSetROIFormat(
                    self._svb_camera_id, 0, 0, self._width, self._height, 1,
                ),
                "SVBSetROIFormat",
            )
            self._resize_frame_buffer()
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if self._svb_camera_id is not None and self._dll is not None:
            if self._streaming:
                try:
                    self._dll.SVBStopVideoCapture(self._svb_camera_id)
                except Exception:  # noqa: BLE001
                    pass
                self._streaming = False
            try:
                self._dll.SVBCloseCamera(self._svb_camera_id)
            except Exception:  # noqa: BLE001
                pass
        self._svb_camera_id = None
        self._dll = None
        self._frame_buffer = None
        self._control_caps.clear()

    def _camera_id(self) -> int:
        if self._svb_camera_id is None:
            raise RuntimeError("SVBONY camera not open")
        return self._svb_camera_id

    def _load_control_caps(self) -> None:
        assert self._dll is not None
        num = c_int()
        _check(self._dll.SVBGetNumOfControls(self._camera_id(), byref(num)), "SVBGetNumOfControls")
        self._control_caps.clear()
        for i in range(num.value):
            caps = _SVB_CONTROL_CAPS()
            _check(self._dll.SVBGetControlCaps(self._camera_id(), i, byref(caps)), "SVBGetControlCaps")
            if caps.ControlType == SVB_EXPOSURE:
                self._control_caps["Exposure"] = caps
            elif caps.ControlType == SVB_GAIN:
                self._control_caps["Gain"] = caps

    def _resize_frame_buffer(self) -> None:
        bytes_per_pixel = max(1, (self._bit_depth + 7) // 8)
        # Demo allocates extra headroom for RAW16 / occasional RGB paths.
        size = self._width * self._height * bytes_per_pixel * 4
        self._frame_buffer = np.empty(size, dtype=np.uint8)

    def set_roi(self, x: int, y: int, width: int, height: int) -> None:
        assert self._dll is not None
        width = max(8, (width // 8) * 8)
        height = max(2, (height // 2) * 2)
        was_streaming = self._streaming
        if was_streaming:
            _check(self._dll.SVBStopVideoCapture(self._camera_id()), "SVBStopVideoCapture")
            self._streaming = False
        try:
            _check(
                self._dll.SVBSetROIFormat(self._camera_id(), x, y, width, height, 1),
                "SVBSetROIFormat",
            )
            self._width, self._height = width, height
            self._resize_frame_buffer()
        finally:
            if was_streaming:
                _check(self._dll.SVBStartVideoCapture(self._camera_id()), "SVBStartVideoCapture")
                self._streaming = True

    def _apply_bit_depth(self, bit_depth: int) -> None:
        assert self._dll is not None
        if bit_depth not in (8, 16):
            raise ValueError(f"unsupported bit depth {bit_depth!r} (must be 8 or 16)")
        img_type = SVB_IMG_RAW8 if bit_depth == 8 else SVB_IMG_RAW16
        _check(self._dll.SVBSetOutputImageType(self._camera_id(), img_type), "SVBSetOutputImageType")
        self._bit_depth = bit_depth

    def set_bit_depth(self, bit_depth: int) -> None:
        assert self._dll is not None
        was_streaming = self._streaming
        if was_streaming:
            _check(self._dll.SVBStopVideoCapture(self._camera_id()), "SVBStopVideoCapture")
            self._streaming = False
        try:
            self._apply_bit_depth(bit_depth)
            self._resize_frame_buffer()
        finally:
            if was_streaming:
                _check(self._dll.SVBStartVideoCapture(self._camera_id()), "SVBStartVideoCapture")
                self._streaming = True

    def set_exposure_us(self, microseconds: int) -> None:
        assert self._dll is not None
        _check(
            self._dll.SVBSetControlValue(self._camera_id(), SVB_EXPOSURE, int(microseconds), SVB_FALSE),
            "SVBSetControlValue EXPOSURE",
        )

    def set_gain(self, gain: int) -> None:
        assert self._dll is not None
        _check(
            self._dll.SVBSetControlValue(self._camera_id(), SVB_GAIN, int(gain), SVB_FALSE),
            "SVBSetControlValue GAIN",
        )

    def get_controls(self) -> dict:
        controls: dict = {}
        for name, caps in self._control_caps.items():
            controls[name] = {
                "Name": name,
                "MinValue": int(caps.MinValue),
                "MaxValue": int(caps.MaxValue),
                "DefaultValue": int(caps.DefaultValue),
            }
        return controls

    def start_streaming(self) -> None:
        assert self._dll is not None
        _check(self._dll.SVBStartVideoCapture(self._camera_id()), "SVBStartVideoCapture")
        self._streaming = True

    def stop_streaming(self) -> None:
        assert self._dll is not None
        _check(self._dll.SVBStopVideoCapture(self._camera_id()), "SVBStopVideoCapture")
        self._streaming = False

    def read_frame(self, timeout_ms: int = 2000) -> np.ndarray:
        assert self._dll is not None and self._frame_buffer is not None
        buf_ptr = self._frame_buffer.ctypes.data_as(POINTER(c_uint8))
        ret = self._dll.SVBGetVideoData(
            self._camera_id(), buf_ptr, c_long(self._frame_buffer.nbytes), int(timeout_ms),
        )
        _check(ret, "SVBGetVideoData")
        width, height = self._width, self._height
        if self._bit_depth == 16:
            pixels = np.frombuffer(self._frame_buffer.data, dtype=np.uint16, count=width * height)
            return pixels.reshape(height, width).copy()
        raw = self._frame_buffer[: width * height]
        return raw.reshape(height, width).copy()

    def get_dropped_frames(self) -> int:
        if self._dll is None or self._svb_camera_id is None:
            return 0
        dropped = c_int()
        if self._dll.SVBGetDroppedFrames(self._svb_camera_id, byref(dropped)) != SVB_SUCCESS:
            return 0
        return int(dropped.value)

    def bayer_pattern_ser_colour_id(self) -> int:
        if not self._is_color:
            return 0
        return _BAYER_TO_SER_COLOUR_ID.get(self._bayer_pattern, 8)

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
