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


def test_empty_answer_after_thinking_retries_without_thinking():
    from types import SimpleNamespace

    from voicenotes.config import Config
    from voicenotes.llm import LLM

    calls = []

    from voicenotes.llm import THINK_BUDGET
    produced = []

    class Stream:  # a model that keeps thinking forever
        closed = False
        def __iter__(self):
            for _ in range(100_000):
                produced.append(1)
                yield SimpleNamespace(choices=[SimpleNamespace(
                    delta=SimpleNamespace(content=None, model_extra={"reasoning_content": "hm"}),
                    finish_reason=None)])
        def close(self):
            Stream.closed = True

    class FakeCompletions:
        def create(self, **kw):
            calls.append(("reasoning_effort" in kw, kw["max_tokens"]))
            if kw.get("stream"):
                return Stream()
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content="Antwoord"), finish_reason="stop")])

    llm = LLM(Config(llm_thinking=True))
    llm._model, llm._context = "fake", 32000
    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    assert llm.chat("sys", "user", max_answer_tokens=500) == "Antwoord"
    # first with thinking, stopped right after the budget, then again without thinking
    assert calls == [(False, 500 + THINK_BUDGET), (True, 500)]
    assert len(produced) <= THINK_BUDGET + 2 and Stream.closed


def test_empty_summary_is_never_saved(tmp_path, monkeypatch):
    import pytest

    from voicenotes import pipeline
    from voicenotes.asr.final import Segment
    from voicenotes.config import Config
    from voicenotes.store import Store

    store = Store(tmp_path / "db.sqlite")
    m = store.create_meeting("M", None, tmp_path / "m")
    store.save_segments(m.id, [Segment(0, 1, "hallo", "A")])
    store.save_summary(m.id, "oude samenvatting")

    class FakeLLM:
        model, context, chunk_chars = "fake", 32000, 90000
        def __init__(self, cfg): pass
        def ensure_context(self, *a, **k): return 32000
        def summarize(self, chunks, on_progress=None, legend=""): return "  "

    monkeypatch.setattr(pipeline, "LLM", FakeLLM)
    with pytest.raises(RuntimeError):
        pipeline.summarize(Config(), store, m.id, progress=lambda _: None)
    assert store.get_meeting(m.id).summary == "oude samenvatting"


def test_keyword_score_finds_misheard_terms():
    from voicenotes.pipeline import keyword_score
    text = "De HAP-data is nog niet compleet, en de koppeling met Westerduin hapert."
    assert keyword_score("Wat werkt er niet bij HAB?", text) > 0.9
    assert keyword_score("Hoe gaat het met Westerdijn?", text) > 0.9
    assert keyword_score("Wat is de begroting?", text) == 0.0
