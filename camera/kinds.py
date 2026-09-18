"""Camera backend identifiers used by ConnectionPanel and CameraWorker."""

CAMERA_KIND_MOCK = "mock"
CAMERA_KIND_ZWO = "zwo"
CAMERA_KIND_SVBONY = "svbony"
CAMERA_KIND_QHY = "qhy"

# Pre-multi-brand UI: connect payloads used "real" for any physical camera.
CAMERA_KIND_ZWO_LEGACY = "real"

# (label shown in the Connection tab dropdown, internal kind value)
CONNECTION_CAMERA_KINDS: list[tuple[str, str]] = [
    ("Mock", CAMERA_KIND_MOCK),
    ("ZWO (ASI)", CAMERA_KIND_ZWO),
    ("SVBONY", CAMERA_KIND_SVBONY),
    ("QHY", CAMERA_KIND_QHY),
]

CONNECTION_CAMERA_KIND_LABELS = [label for label, _ in CONNECTION_CAMERA_KINDS]
KIND_TO_CONNECTION_LABEL = {kind: label for label, kind in CONNECTION_CAMERA_KINDS}
LABEL_TO_CAMERA_KIND = {label: kind for label, kind in CONNECTION_CAMERA_KINDS}


def normalize_camera_kind(kind: str) -> str:
    if kind == CAMERA_KIND_ZWO_LEGACY:
        return CAMERA_KIND_ZWO
    return kind


def is_mock_camera_kind(kind: str) -> bool:
    return normalize_camera_kind(kind) == CAMERA_KIND_MOCK
