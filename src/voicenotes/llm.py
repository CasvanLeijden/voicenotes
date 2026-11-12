"""LM Studio client (OpenAI-compatible) for summaries, Q&A and embeddings."""
from __future__ import annotations

import re

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

ASK_PROMPT = """Je beantwoordt vragen over meetings op basis van transcriptfragmenten.
Antwoord in het Nederlands, kort en concreet. Noem wie iets zei als dat relevant is.
Staat het antwoord niet in de fragmenten, zeg dat dan eerlijk."""

TITLE_PROMPT = """Bedenk een korte, specifieke titel voor deze meeting: 2 tot 6 woorden die het
belangrijkste onderwerp noemen (bijvoorbeeld een project, klant of besluit), geen algemene titel
als "Vergadering" of "Teamoverleg".
Schrijf de titel in deze taal: {language}. Dat is de taal die in de meeting gesproken werd,
ook als de samenvatting in een andere taal is.
Geef alleen de titel, zonder aanhalingstekens, punt of uitleg."""

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


DEFAULT_CONTEXT = 4096
# Room for the system prompt, the instructions and the model's answer.
RESERVED_TOKENS = 1800
# Dutch transcript text is roughly 3 characters per token for these tokenizers (conservative).
CHARS_PER_TOKEN = 3.0


def loaded_models(base_url: str, timeout: float = 3.0) -> list[dict]:
    """LM Studio's native API lists loaded instances with their context length."""
    import json
    import urllib.request

    root = base_url.rstrip("/").removesuffix("/v1")
    try:
        with urllib.request.urlopen(f"{root}/api/v0/models", timeout=timeout) as r:
            data = json.load(r)["data"]
    except Exception:
        return []
    return [m for m in data if m.get("state") == "loaded" and m.get("type") in ("llm", "vlm")]


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
             think: bool | None = None) -> str:
        """think: None = follow the llm_thinking setting."""
        think = self.cfg.llm_thinking if think is None else think
        kwargs = dict(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=temperature,
        )
        # Without thinking, reasoning models answer ~5x faster (most tokens go to thinking),
        # but summaries are a bit less careful. Default: think.
        if think:
            resp = self.client.chat.completions.create(**kwargs)
        elif self._reasoning_off is not False:
            try:
                resp = self.client.chat.completions.create(reasoning_effort="none", **kwargs)
                self._reasoning_off = True
            except Exception:
                self._reasoning_off = False   # model/server doesn't accept it: plain request
                resp = self.client.chat.completions.create(**kwargs)
        else:
            resp = self.client.chat.completions.create(**kwargs)
        text = resp.choices[0].message.content or ""
        return _THINK_RE.sub("", text).strip()

    def summarize(self, chunks: list[str], on_progress=None) -> str:
        if len(chunks) == 1:
            notes = [chunks[0]]
        else:
            notes = []
            for i, chunk in enumerate(chunks, 1):
                if on_progress:
                    on_progress(f"Deel {i}/{len(chunks)} samenvatten")
                notes.append(self.chat(MAP_PROMPT, chunk))
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
            notes = [self.chat(MAP_PROMPT, "\n\n".join(g)) for g in groups]
        label = "Transcript" if len(chunks) == 1 else "Notities"
        return self.chat(REDUCE_PROMPT, f"{label}:\n\n" + "\n\n---\n\n".join(notes))

    def title(self, transcript_excerpt: str, summary: str = "", language: str = "nl") -> str:
        from .asr.final import language_name
        name = "Nederlands" if language == "nl" else language_name(language).capitalize()
        user = f"Transcript (begin):\n{transcript_excerpt}"
        if summary:
            user += f"\n\nSamenvatting:\n{summary}"
        return clean_title(self.chat(TITLE_PROMPT.format(language=name), user, temperature=0.3))

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
        return self.chat(ASK_PROMPT, f"Fragmenten:\n\n{ctx}\n\nVraag: {question}")


def clean_title(raw: str, max_len: int = 70) -> str:
    """First line, without labels, quotes, markdown or a trailing period."""
    line = next((ln for ln in (raw or "").splitlines() if ln.strip()), "").strip()
    line = re.sub(r"^(titel|title)\s*:\s*", "", line, flags=re.I)
    quotes = "*#_`'\"“”‘’ "
    line = line.strip(quotes).rstrip(".").strip(quotes).strip()
    if len(line) > max_len:
        line = line[:max_len].rsplit(" ", 1)[0]
    return line
