"""LM Studio client (OpenAI-compatible) for summaries, Q&A and embeddings."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np
from openai import OpenAI

from .config import Config

MAP_PROMPT = """Je krijgt een deel van een transcript van een Nederlandstalige meeting.
Maak beknopte notities in het Nederlands: de besproken onderwerpen, genomen besluiten,
actiepunten (wie, wat, wanneer als dat genoemd is) en open vragen.
Verzin niets dat niet in het transcript staat. Transcriptiefouten mag je stilzwijgend corrigeren."""

REDUCE_PROMPT = """Je krijgt notities van opeenvolgende delen van één meeting.
Schrijf in het Nederlands een samenvatting in Markdown met precies deze kopjes:

## Samenvatting
(3 tot 6 zinnen)

## Besluiten
(lijst, of "Geen")

## Actiepunten
(lijst als "- [ ] Wie: wat (deadline)", of "Geen")

## Open vragen
(lijst, of "Geen")

Verzin niets dat niet in de notities staat."""

ASK_PROMPT = """Je beantwoordt vragen over meetings op basis van hun transcripten.
Antwoord in het Nederlands, concreet en volledig. Noem wie iets zei als dat relevant is.
Let op: het transcript is automatisch gemaakt en kan namen, afkortingen en vaktermen verkeerd
gespeld hebben. Zoek daarom ook naar woorden die erop lijken of hetzelfde klinken (bijvoorbeeld
"HAP" voor "HAB", of "Westerduin" voor "Westerdijn") en ga ervan uit dat die bedoeld worden.
Staat het antwoord echt niet in het transcript, zeg dat dan eerlijk."""

TITLE_PROMPT = """Bedenk een korte, specifieke titel voor deze meeting: 2 tot 6 woorden die het
belangrijkste onderwerp noemen (bijvoorbeeld een project, klant of besluit), geen algemene titel
als "Vergadering" of "Teamoverleg".
Schrijf de titel in deze taal: {language}. Dat is de taal die in de meeting gesproken werd,
ook als de samenvatting in een andere taal is.
Geef alleen de titel, zonder aanhalingstekens, punt of uitleg."""

CODES_NOTE = """
Sprekers staan in het transcript als codes, zoals [S1] en [S2]. Verwijs in je tekst naar
mensen altijd met die code, inclusief de vierkante haken (bijvoorbeeld "[S2] stelde voor om…"
of "- [ ] [S1]: offerte sturen"). Schrijf nooit hun naam uit; de namen worden later ingevuld."""

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


DEFAULT_CONTEXT = 4096
# Max tokens a model may spend thinking before it must answer (~1-2 min on an M4).
THINK_BUDGET = 2048
SUMMARY_TOKENS = 3000   # room for a full meeting summary
# Room for the system prompt, the instructions and the model's answer.
RESERVED_TOKENS = 1800
# Dutch transcript text is roughly 3 characters per token for these tokenizers (conservative).
CHARS_PER_TOKEN = 3.0


LMS_CLI = Path.home() / ".lmstudio" / "bin" / "lms"


def lm_models(base_url: str, timeout: float = 3.0) -> list[dict]:
    """All chat models LM Studio knows (loaded or not), from its native API."""
    import json
    import urllib.request

    root = base_url.rstrip("/").removesuffix("/v1")
    try:
        with urllib.request.urlopen(f"{root}/api/v0/models", timeout=timeout) as r:
            data = json.load(r)["data"]
    except Exception:
        return []
    return [m for m in data if m.get("type") in ("llm", "vlm")]


def loaded_models(base_url: str, timeout: float = 3.0) -> list[dict]:
    """Loaded instances with their context length."""
    return [m for m in lm_models(base_url, timeout) if m.get("state") == "loaded"]


def chunk_chars_for(context: int) -> int:
    return max(3000, int((context - RESERVED_TOKENS) * CHARS_PER_TOKEN))


class LLM:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.client = OpenAI(base_url=cfg.lmstudio_url, api_key="lm-studio")
        self._model = cfg.llm_model or None
        self._context: int | None = None
        self._reasoning_off: bool | None = None

    def _resolve(self) -> None:
        loaded = loaded_models(self.cfg.lmstudio_url)
        if not loaded or (self._model and not any(m["id"] == self._model for m in loaded)):
            # Nothing (or not the chosen model) loaded: let LM Studio load it now, so we can
            # read the real context length instead of guessing a small default.
            self._trigger_load()
            loaded = loaded_models(self.cfg.lmstudio_url)
        if self._model is None:
            if loaded:
                # Several instances loaded: use the one with the most context.
                best = max(loaded, key=lambda m: m.get("loaded_context_length") or 0)
                self._model = best["id"]
            else:
                ids = [m.id for m in self.client.models.list().data]
                llms = [i for i in ids if "embed" not in i.lower()]
                if not llms:
                    raise RuntimeError("Geen LLM gevonden in LM Studio. Laad een model en start de server.")
                self._model = llms[0]
        match = [m for m in loaded if m["id"] == self._model]
        self._context = (match[0].get("loaded_context_length") if match else None) or DEFAULT_CONTEXT

    def _trigger_load(self) -> None:
        target = self._model
        if target is None:
            available = lm_models(self.cfg.lmstudio_url)
            if not available:
                return
            target = available[0]["id"]
        try:  # LM Studio loads a model on first use ("just in time")
            self.client.chat.completions.create(
                model=target, messages=[{"role": "user", "content": "ok"}], max_tokens=1)
        except Exception:
            pass

    def ensure_context(self, needed_tokens: int, on_progress=None) -> int:
        """Make sure the loaded model has at least `needed_tokens` of context, reloading it
        with a bigger context through the `lms` CLI if necessary. Returns the context."""
        if self.context >= needed_tokens or not LMS_CLI.exists():
            return self.context
        info = next((m for m in lm_models(self.cfg.lmstudio_url) if m["id"] == self._model), {})
        max_ctx = info.get("max_context_length") or needed_tokens
        new_ctx = min(max_ctx, max(32_768, int(needed_tokens * 1.25)))
        key = self._model.split(":")[0]   # instance "google/gemma-4-e4b:2" -> model "google/gemma-4-e4b"
        if on_progress:
            on_progress(f"Model opnieuw laden met {new_ctx} tokens context")
        subprocess.run([str(LMS_CLI), "unload", self._model], capture_output=True, timeout=60)
        subprocess.run([str(LMS_CLI), "load", key, "--context-length", str(new_ctx),
                        "--identifier", self._model, "-y"], capture_output=True, timeout=300)
        self._context = None
        self._resolve()
        return self.context

    @property
    def model(self) -> str:
        if self._model is None or self._context is None:
            self._resolve()
        return self._model

    @property
    def context(self) -> int:
        if self._context is None:
            self._resolve()
        return self._context

    @property
    def chunk_chars(self) -> int:
        return self.cfg.chunk_chars_override or chunk_chars_for(self.context)

    def chat(self, system: str, user: str, temperature: float = 0.2,
             think: bool | None = None, max_answer_tokens: int = 1500) -> str:
        """think: None = follow the llm_thinking setting.

        Output is always capped: max_answer_tokens for the answer, plus THINK_BUDGET when the
        model may think first. Some models (Qwen3.5-9B) otherwise think for 10+ minutes and
        return nothing; if the thinking budget runs out, we ask again without thinking.
        """
        think = self.cfg.llm_thinking if think is None else think
        kwargs = dict(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=temperature,
            max_tokens=max_answer_tokens + (THINK_BUDGET if think else 0),
        )
        if think:
            text, finished = self._chat_thinking(kwargs)
            if text and finished:
                return text
            print(f"{self.model}: denkbudget op of geen antwoord, opnieuw zonder nadenken")
            return self.chat(system, user, temperature=temperature, think=False,
                             max_answer_tokens=max_answer_tokens)
        if self._reasoning_off is not False:
            try:
                resp = self.client.chat.completions.create(reasoning_effort="none", **kwargs)
                self._reasoning_off = True
            except Exception:
                self._reasoning_off = False   # model/server doesn't accept it: plain request
                resp = self.client.chat.completions.create(**kwargs)
        else:
            resp = self.client.chat.completions.create(**kwargs)
        return _THINK_RE.sub("", resp.choices[0].message.content or "").strip()

    def _chat_thinking(self, kwargs: dict) -> tuple[str, bool]:
        """Stream the answer and stop as soon as the thinking exceeds THINK_BUDGET.
        Closing the stream makes LM Studio stop generating. Returns (text, finished)."""
        stream = self.client.chat.completions.create(stream=True, **kwargs)
        thinking = 0
        inline = False   # thinking inside <think>...</think> in the content (some templates)
        parts: list[str] = []
        finish = None
        try:
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                extra = getattr(delta, "model_extra", None) or {}
                reasoning = extra.get("reasoning_content") or extra.get("reasoning") or ""
                content = delta.content or ""
                if content:
                    parts.append(content)
                    inline = ("<think>" in content or inline) and "</think>" not in content
                if reasoning or inline:
                    thinking += 1   # LM Studio streams about one token per chunk
                if thinking > THINK_BUDGET:
                    return "", False
                finish = chunk.choices[0].finish_reason or finish
        finally:
            stream.close()
        text = _THINK_RE.sub("", "".join(parts)).strip()
        return text, finish != "length"

    def summarize(self, chunks: list[str], on_progress=None, legend: str = "") -> str:
        map_prompt, reduce_prompt = MAP_PROMPT + CODES_NOTE, REDUCE_PROMPT + CODES_NOTE
        who = f"Sprekers:\n{legend}\n\n" if legend else ""
        if len(chunks) == 1:
            notes = [chunks[0]]
        else:
            notes = []
            for i, chunk in enumerate(chunks, 1):
                if on_progress:
                    on_progress(f"Deel {i}/{len(chunks)} samenvatten")
                notes.append(self.chat(map_prompt, who + chunk, max_answer_tokens=SUMMARY_TOKENS))
        if on_progress:
            on_progress("Eindsamenvatting maken")
        # Reduce in rounds when the notes are too long for the context window.
        limit = self.chunk_chars
        while len(notes) > 1 and sum(len(n) for n in notes) > limit:
            groups, cur = [], []
            for n in notes:
                if cur and sum(len(x) for x in cur) + len(n) > limit:
                    groups.append(cur)
                    cur = []
                cur.append(n)
            groups.append(cur)
            if len(groups) == len(notes):  # can't shrink by grouping; stop
                break
            notes = [self.chat(map_prompt, who + "\n\n".join(g), max_answer_tokens=SUMMARY_TOKENS)
                     for g in groups]
        label = "Transcript" if len(chunks) == 1 else "Notities"
        return self.chat(reduce_prompt, who + f"{label}:\n\n" + "\n\n---\n\n".join(notes),
                         max_answer_tokens=SUMMARY_TOKENS)

    def title(self, transcript_excerpt: str, summary: str = "", language: str = "nl") -> str:
        from .asr.final import language_name
        name = "Nederlands" if language == "nl" else language_name(language).capitalize()
        user = f"Transcript (begin):\n{transcript_excerpt}"
        if summary:
            user += f"\n\nSamenvatting:\n{summary}"
        return clean_title(self.chat(TITLE_PROMPT.format(language=name), user, temperature=0.3,
                                     max_answer_tokens=60))

    def embed(self, texts: list[str], *, query: bool = False) -> np.ndarray:
        # nomic-embed expects task prefixes.
        prefix = "search_query: " if query else "search_document: "
        if "nomic" in self.cfg.embed_model:
            texts = [prefix + t for t in texts]
        resp = self.client.embeddings.create(model=self.cfg.embed_model, input=texts)
        vecs = np.array([d.embedding for d in resp.data], dtype=np.float32)
        return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)

    def answer(self, question: str, context: list[str]) -> str:
        ctx = "\n\n---\n\n".join(context)
        return self.chat(ASK_PROMPT, f"Transcript(en):\n\n{ctx}\n\nVraag: {question}",
                         max_answer_tokens=1500)


def clean_title(raw: str, max_len: int = 70) -> str:
    """First line, without labels, quotes, markdown or a trailing period."""
    line = next((ln for ln in (raw or "").splitlines() if ln.strip()), "").strip()
    line = re.sub(r"^(titel|title)\s*:\s*", "", line, flags=re.I)
    quotes = "*#_`'\"“”‘’ "
    line = line.strip(quotes).rstrip(".").strip(quotes).strip()
    if len(line) > max_len:
        line = line[:max_len].rsplit(" ", 1)[0]
    return line
