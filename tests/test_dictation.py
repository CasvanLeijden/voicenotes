from voicenotes.dictation import strip_fillers


def test_strip_fillers():
    assert strip_fillers("Ik denk dat we, um, dat we morgen gaan.") == "Ik denk dat we dat we morgen gaan."
    assert strip_fillers("Eh, ik bel je straks.") == "Ik bel je straks."
    assert strip_fillers("Hij zei ehm nee.") == "Hij zei nee."


def test_strip_fillers_keeps_real_words():
    assert strip_fillers("Umbrella en heh en Ehmke blijven staan.") == "Umbrella en heh en Ehmke blijven staan."
