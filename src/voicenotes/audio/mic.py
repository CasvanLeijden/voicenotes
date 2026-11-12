"""Microphone capture via the Swift `audiotap --mic` helper (AVAudioEngine).

Capturing in a separate process keeps PortAudio/Core Audio out of the app process: starting
or stopping a PortAudio stream right after another Core Audio client exits can hang for
seconds, which made the app freeze and clipped the first words of dictation.
"""
from __future__ import annotations

from typing import Callable

import numpy as np

from .helper_stream import HelperStream


class Mic(HelperStream):
    def __init__(self, on_audio: Callable[[np.ndarray, int], None]):
        super().__init__(["--mic"], on_audio)
