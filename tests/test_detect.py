from voicenotes.detect import CallDetector, app_for_bundle

TEAMS = {"pid": 10, "bundle": "com.microsoft.teams2.helper"}
WA = {"pid": 20, "bundle": "net.whatsapp.WhatsApp"}
OTHER = {"pid": 30, "bundle": "com.electron.wispr-flow.helper"}


def run(det, timeline):
    """timeline: list of (time, users). Returns [(time, kind, app)]."""
    out = []
    for t, users in timeline:
        out += [(t, e.kind, e.app) for e in det.update(t, users)]
    return out


def test_app_matching():
    assert app_for_bundle("com.microsoft.teams2") == "Teams"
    assert app_for_bundle("com.microsoft.teams2.helper") == "Teams"
    assert app_for_bundle("com.google.Chrome.helper") == "Chrome"
    assert app_for_bundle("com.microsoft.teams2x") is None
    assert app_for_bundle("com.electron.wispr-flow") is None


def test_call_starts_after_delay_and_ends_after_idle():
    det = CallDetector(start_delay=3, end_delay=10)
    events = run(det, [(0, [TEAMS]), (2, [TEAMS]), (4, [TEAMS]), (6, []), (12, []), (15, [])])
    # last mic use at t=4; at t=15 it has been idle for 11 s (>= 10)
    assert events == [(4, "started", "Teams"), (15, "ended", "Teams")]


def test_short_mic_blip_is_not_a_call():
    det = CallDetector(start_delay=3)
    assert run(det, [(0, [TEAMS]), (2, []), (4, [TEAMS]), (6, [])]) == []


def test_brief_dropout_during_call_does_not_end_it():
    det = CallDetector(start_delay=3, end_delay=10)
    events = run(det, [(0, [WA]), (4, [WA]), (6, []), (10, []), (12, [WA]), (30, [WA])])
    assert events == [(4, "started", "WhatsApp")]
    assert det.active_calls == ["WhatsApp"]


def test_unknown_apps_and_own_process_are_ignored():
    det = CallDetector(start_delay=1, ignore_pids={10})
    assert run(det, [(0, [TEAMS, OTHER]), (5, [TEAMS, OTHER])]) == []


def test_two_apps_tracked_independently():
    det = CallDetector(start_delay=1, end_delay=5)
    events = run(det, [(0, [TEAMS]), (2, [TEAMS, WA]), (4, [WA]), (10, [WA])])
    assert (2, "started", "Teams") in events
    assert (4, "started", "WhatsApp") in events
    assert (10, "ended", "Teams") in events
