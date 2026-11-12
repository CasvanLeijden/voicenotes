"""Voice recognition: link voices in a meeting to known people via embedding similarity."""
from __future__ import annotations

import numpy as np

from .store import Store

DEFAULT_THRESHOLD = 0.55
# Shorter speakers give unreliable voice prints; don't let them shape a person's voice.
MIN_LEARN_SECONDS = 20.0


def _norm(v: np.ndarray) -> np.ndarray:
    return v / (np.linalg.norm(v) + 1e-9)


def match(store: Store, embeddings: dict[str, np.ndarray],
          threshold: float = DEFAULT_THRESHOLD) -> dict[str, int | None]:
    """Map diarization labels to person ids (None = unknown voice).

    Greedy one-to-one matching on cosine similarity.
    """
    profiles = store.voice_profiles()
    pairs = []
    for label, emb in embeddings.items():
        e = _norm(emb)
        for pid, _name, pemb, _ in profiles:
            pairs.append((float(e @ _norm(pemb)), label, pid))
    pairs.sort(reverse=True)

    result: dict[str, int | None] = {label: None for label in embeddings}
    used: set[int] = set()
    for sim, label, pid in pairs:
        if sim < threshold:
            break
        if result[label] is not None or pid in used:
            continue
        result[label] = pid
        used.add(pid)
    return result


def learn(store: Store, person_id: int, embedding: np.ndarray) -> None:
    """Add a voice sample to a person (running mean)."""
    person = store.get_person(person_id)
    e = _norm(embedding.astype(np.float32))
    if person["embedding"] is None:
        store.set_person_voice(person_id, e, 1)
        return
    old = np.frombuffer(person["embedding"], dtype=np.float32)
    n = max(person["samples"], 1)
    store.set_person_voice(person_id, _norm((old * n + e) / (n + 1)), n + 1)
