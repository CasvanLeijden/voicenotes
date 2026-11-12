"""System audio capture via the Swift `audiotap` helper (Core Audio global tap)."""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Callable

import numpy as np

_REPO_BINARY = (Path(__file__).resolve().parents[3]
                / "swift" / "audiotap" / ".build" / "release" / "audiotap")


def find_audiotap() -> Path:
    candidates = [os.environ.get("VOICENOTES_AUDIOTAP"), str(_REPO_BINARY), shutil.which("audiotap")]
    for c in candidates:
        if c and Path(c).is_file():
            return Path(c)
    raise FileNotFoundError(
        "audiotap-helper niet gevonden. Bouw hem met: "
        "cd swift/audiotap && swift build -c release")


def SystemTap(on_audio: Callable[[np.ndarray, int], None]):  # noqa: N802 (kept as a factory)
    from .helper_stream import HelperStream
    return HelperStream([], on_audio)
