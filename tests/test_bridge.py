"""Qt converts JS arguments to the declared slot types: a wrong type silently turns
"Spreker 1" into a number. Check each bridge slot's declared types against its Python signature."""
import inspect

from PySide6.QtCore import QMetaMethod

from voicenotes.ui.bridge import Bridge

QT_TYPES = {"int": int, "QString": str, "bool": bool, "double": float}


def test_slot_types_match_python_annotations():
    mo = Bridge.staticMetaObject
    checked = 0
    for i in range(mo.methodOffset(), mo.methodCount()):
        m = mo.method(i)
        if m.methodType() != QMetaMethod.MethodType.Slot:
            continue
        name = bytes(m.name()).decode()
        hints = inspect.get_annotations(getattr(Bridge, name), eval_str=True)
        params = [p for p in inspect.signature(getattr(Bridge, name)).parameters if p != "self"]
        qt_types = [bytes(t).decode() for t in m.parameterTypes()]
        assert len(qt_types) == len(params), name
        for p, qt in zip(params, qt_types):
            assert QT_TYPES[qt] is hints[p], f"{name}({p}): slot says {qt}, function expects {hints[p]}"
        checked += 1
    assert checked > 10


def test_get_meeting_fills_in_current_names(tmp_path):
    import json
    from types import SimpleNamespace

    from voicenotes.asr.final import Segment
    from voicenotes.config import Config
    from voicenotes.store import MIC_LABEL, Store

    store = Store(tmp_path / "db.sqlite", my_name="Cas")
    me = store.ensure_me("Cas")
    jan = store.create_person("Jan")
    m = store.create_meeting("M", "recording", tmp_path / "m")
    store.save_meeting_speakers(m.id, [(MIC_LABEL, "Cas", me, None), ("SPEAKER_00", "Spreker 1", jan, None)])
    store.save_segments(m.id, [Segment(0, 1, "hoi", MIC_LABEL), Segment(1, 2, "hallo", "SPEAKER_00")])
    store.assign_tags(m.id)
    store.save_summary(m.id, "[S2] belt [S1] terug.")
    ctl = SimpleNamespace(store=store, cfg=Config(my_name="Cas"), busy=set(), rec_meeting=None,
                          progress_text={})
    data = json.loads(Bridge(ctl).getMeeting(m.id))
    assert "Jan belt Cas terug." in data["summaryHtml"]
    assert [s["speaker"] for s in data["segments"]] == ["Cas", "Jan"]
    assert {s["name"] for s in data["speakers"]} == {"Cas", "Jan"}
    assert data["me"] == "Cas"
