"""Voice profiles: remember speakers across meetings via embedding similarity."""
from __future__ import annotations

import numpy as np

from .store import Store

DEFAULT_THRESHOLD = 0.55


def _norm(v: np.ndarray) -> np.ndarray:
    return v / (np.linalg.norm(v) + 1e-9)


def match(store: Store, embeddings: dict[str, np.ndarray],
          threshold: float = DEFAULT_THRESHOLD) -> dict[str, tuple[str, int | None]]:
    """Map diarization labels to (display name, profile id).

    Greedy one-to-one matching on cosine similarity; unknown voices become "Spreker N".
    """
    profiles = store.get_profiles()
    pairs = []
    for label, emb in embeddings.items():
        e = _norm(emb)
        for pid, name, pemb, _ in profiles:
            pairs.append((float(e @ _norm(pemb)), label, pid, name))
    pairs.sort(reverse=True)

    result: dict[str, tuple[str, int | None]] = {}
    used: set[int] = set()
    for sim, label, pid, name in pairs:
        if sim < threshold:
            break
        if label in result or pid in used:
            continue
        result[label] = (name, pid)
        used.add(pid)

    n = 1
    for label in sorted(embeddings):
        if label not in result:
            result[label] = (f"Spreker {n}", None)
            n += 1
    return result


def learn(store: Store, name: str, embedding: np.ndarray) -> int:
    """Add a voice sample to the named profile (running mean), creating it if needed."""
    existing = store.get_profile_by_name(name)
    e = _norm(embedding.astype(np.float32))
    if existing is None:
        return store.create_profile(name, e)
    pid, _, pemb, n = existing
    store.update_profile(pid, _norm((pemb * n + e) / (n + 1)), n + 1)
    return pid
