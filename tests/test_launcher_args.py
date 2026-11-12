import sys

from voicenotes.app import _run_as_python


def test_interpreter_style_calls_are_not_app_starts(tmp_path, monkeypatch):
    out = tmp_path / "ran.txt"
    code = f"open({str(out)!r}, 'w').write('ja')"
    monkeypatch.setattr(sys, "argv", list(sys.argv))
    assert _run_as_python(["Voicenotes", "-s", "-c", code]) is True
    assert out.read_text() == "ja"


def test_app_starts_are_left_alone():
    assert _run_as_python(["Voicenotes"]) is False
    assert _run_as_python(["Voicenotes", "--hidden"]) is False
