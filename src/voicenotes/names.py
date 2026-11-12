"""Speaker codes in generated text.

Summaries are stored with codes like [S2] instead of names. They are filled in with the
current names whenever they are shown, so renaming or merging people never requires a new
summary.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .store import Speaker

TAG_RE = re.compile(r"\[(S\d+)\]")
# Words that are too common to safely treat as a name when converting old text.
_UNSAFE = {"ik", "jij", "wij", "we", "hij", "zij", "u", "anderen", "iedereen", "team"}


def tag_names(speakers: list["Speaker"]) -> dict[str, str]:
    """tag -> current display name."""
    return {sp.tag: sp.name for sp in speakers if sp.tag}


def render(text: str, speakers: list["Speaker"]) -> str:
    """Replace [S2] codes with the current names."""
    names = tag_names(speakers)
    return TAG_RE.sub(lambda m: names.get(m.group(1), m.group(0)), text or "")


def names_to_tags(text: str, speakers: list["Speaker"]) -> str:
    """Turn names written out in (older or disobedient) generated text back into codes.
    Only distinctive names are converted: never words like "Ik" or "we"."""
    if not text:
        return text
    pairs = []
    for sp in speakers:
        if not sp.tag:
            continue
        for name in {sp.name, sp.default_name}:
            if name and len(name) >= 3 and name.lower() not in _UNSAFE:
                pairs.append((name, sp.tag))
    for name, tag in sorted(pairs, key=lambda p: -len(p[0])):   # longest names first
        text = re.sub(rf"(?<![\w\[]){re.escape(name)}(?![\w\]])", f"[{tag}]", text)
    return text


def speaker_legend(speakers: list["Speaker"]) -> str:
    """Explains the codes to the language model."""
    lines = []
    for sp in speakers:
        if not sp.tag:
            continue
        who = sp.name
        if sp.is_me:
            who += " (de gebruiker zelf, via de microfoon)"
        elif not sp.person_id:
            who += " (naam nog onbekend)"
        lines.append(f"[{sp.tag}] = {who}")
    return "\n".join(lines)
