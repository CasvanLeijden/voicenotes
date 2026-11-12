"""Settings, stored as TOML in the app support directory."""
from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

from platformdirs import user_data_dir

DATA_DIR = Path(os.environ.get("VOICENOTES_HOME") or user_data_dir("Voicenotes", appauthor=False))
CONFIG_PATH = DATA_DIR / "config.toml"


@dataclass
class Config:
    my_name: str = "Ik"
    language: str = "nl"
    whisper_model: str = "mlx-community/whisper-large-v3-mlx"
    # Names and jargon that help whisper spell things correctly.
    vocabulary: list[str] = field(default_factory=list)
    live_transcription: bool = True
    live_model: str = "mlx-community/parakeet-tdt-0.6b-v3"
    dictation: bool = True
    # Accurate: whisper-turbo fixed to Dutch, uses the vocabulary (~1 s per sentence).
    # Fastest: parakeet (~0.2 s), guesses the language itself and can slip on short phrases.
    dictation_model: str = "mlx-community/whisper-large-v3-turbo"
    # Tidy dictated text with the LM Studio model (punctuation, filler words). Adds ~1 s.
    dictation_cleanup: bool = False
    # Appearance: "dark" (default), "light" or "system".
    theme: str = "dark"
    # Keep Voicenotes windows out of screen sharing and screenshots.
    hide_from_screen_share: bool = True
    detect_calls: bool = True
    # Per call app (display name, e.g. "Teams"): start recording automatically, or never ask.
    auto_record_apps: list[str] = field(default_factory=list)
    ignored_apps: list[str] = field(default_factory=list)
    diarize: bool = True
    # Cosine similarity needed to recognise a saved voice profile (0-1).
    speaker_threshold: float = 0.55
    lmstudio_url: str = "http://localhost:1234/v1"
    # Empty = use the loaded LLM with the most context.
    llm_model: str = ""
    # Let reasoning models think before answering. Off by default: with Qwen3.5-9B thinking
    # took 10+ minutes and returned nothing; without it a full summary takes ~25 s.
    llm_thinking: bool = False
    embed_model: str = "text-embedding-nomic-embed-text-v1.5"
    # Characters per transcript chunk when summarizing. 0 = derive from the context length
    # that is loaded in LM Studio.
    chunk_chars_override: int = 0

    @property
    def meetings_dir(self) -> Path:
        return DATA_DIR / "meetings"

    @property
    def db_path(self) -> Path:
        return DATA_DIR / "voicenotes.db"


def _toml_value(v) -> str:
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    return str(v)


def load() -> Config:
    if not CONFIG_PATH.exists():
        cfg = Config()
        save(cfg)
        return cfg
    data = tomllib.loads(CONFIG_PATH.read_text())
    known = {k: v for k, v in data.items() if k in Config.__dataclass_fields__}
    return Config(**known)


def save(cfg: Config) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lines = [f"{k} = {_toml_value(v)}" for k, v in asdict(cfg).items()]
    CONFIG_PATH.write_text("\n".join(lines) + "\n")
