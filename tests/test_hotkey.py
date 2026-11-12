from voicenotes.hotkey import FnStateMachine


def test_hold_is_push_to_talk():
    m = FnStateMachine()
    assert m.fn_down(0.0) == ["start"]
    assert m.fn_up(1.2) == ["stop"]
    assert m.state == "idle"


def test_single_short_tap_cancels():
    m = FnStateMachine()
    m.fn_down(0.0)
    assert m.fn_up(0.1) == ["cancel"]


def test_double_tap_starts_handsfree_until_next_press():
    m = FnStateMachine()
    m.fn_down(0.0); m.fn_up(0.1)                 # first tap: cancelled
    assert m.fn_down(0.3) == ["start"]           # second tap starts recording again
    assert m.fn_up(0.4) == ["handsfree"]         # ...and keeps going
    assert m.other_key(2.0) == []                # typing doesn't stop hands-free
    assert m.fn_down(9.0) == ["stop"]
    assert m.fn_up(9.1) == []
    assert m.state == "idle"


def test_slow_second_tap_is_not_double_tap():
    m = FnStateMachine()
    m.fn_down(0.0); m.fn_up(0.1)
    m.fn_down(1.0)
    assert m.fn_up(1.1) == ["cancel"]


def test_fn_as_modifier_cancels():
    m = FnStateMachine()
    m.fn_down(0.0)
    assert m.other_key(0.5) == ["cancel"]
    assert m.fn_up(1.0) == []                    # release after a shortcut does nothing
    assert m.state == "idle"
