"""The fast model is loaded in one thread (preload at startup) and used from others
(dictation, live transcription). MLX ties work to the thread that created the model."""
import shutil
import threading

import pytest

pytestmark = pytest.mark.skipif(shutil.which("say") is None, reason="needs macOS say")


def test_load_in_one_thread_use_in_others(tmp_path):
    import subprocess

    from voicenotes.asr.live import get_fast_asr
    from voicenotes.audio.importer import load_audio

    wav = tmp_path / "zin.aiff"
    subprocess.run(["say", "-v", "Xander", "-o", str(wav), "Dit is een korte testzin."], check=True)
    audio = load_audio(wav)

    loader = threading.Thread(target=get_fast_asr)   # like the preload at startup
    loader.start(); loader.join()

    results, errors = [], []

    def use():
        try:
            results.append(get_fast_asr()(audio))
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    users = [threading.Thread(target=use) for _ in range(3)]  # dictation + live, concurrently
    for t in users: t.start()
    for t in users: t.join()
    assert errors == []
    assert len(results) == 3 and all("korte" in r.lower() for r in results), results
