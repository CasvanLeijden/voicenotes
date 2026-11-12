from voicenotes.llm import clean_title
from voicenotes.store import Store


def test_clean_title():
    assert clean_title('Titel: "Lancering klantportaal op 15 maart".') == "Lancering klantportaal op 15 maart"
    assert clean_title("**Q3 budget review**\nExtra uitleg") == "Q3 budget review"
    assert clean_title("") == ""
    long = "woord " * 30
    assert len(clean_title(long)) <= 70 and not clean_title(long).endswith(" ")


def test_title_auto_until_renamed_by_user(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    m = store.create_meeting("Opname 11-11 14:00", "recording", tmp_path / "m", title_auto=True)
    assert store.get_meeting(m.id).title_auto
    store.rename_meeting(m.id, "Planning Q3", auto=True)       # generated title
    assert store.get_meeting(m.id).title_auto
    store.rename_meeting(m.id, "Mijn eigen titel")              # chosen by the user
    got = store.get_meeting(m.id)
    assert got.title == "Mijn eigen titel" and not got.title_auto
