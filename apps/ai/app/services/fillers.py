"""
Pre-recorded waiting phrases ("Je note.", "Un instant.") played the moment the model calls a tool before saying
anything: the caller hears something within ~0.1 s, however long the tools take.

They are synthesized ONCE per TTS voice (warm(), at the start of an outbound call) and kept in memory, so playing
one costs no TTS call. pick() never returns the same phrase twice in a row. The model is told not to write a
waiting phrase itself (agent/prompts.py OUTBOUND_STYLE): the switchboard does it.
"""

import asyncio
import random
from dataclasses import dataclass

from app.clients import TTSClient
from app.config import settings

PHRASES = ["Je note.", "Un instant.", "Je regarde ça.", "Ça marche.", "D'accord, je vérifie.", "Très bien."]


@dataclass(frozen=True)
class Clip:
    text: str
    mime: str
    audio: bytes


_clips: dict[tuple[str, ...], list[Clip]] = {}  # (provider, model, voice[, "phone"]) → clips
_lock = asyncio.Lock()


def _key(tts: TTSClient) -> tuple[str, ...]:
    """One set of clips per voice AND audio format: the phone (µ-law) clips are not the browser's (mp3) ones."""
    key = (tts.name, getattr(tts, "model", ""), settings.tts.voice or getattr(tts, "voice", ""))
    return (*key, "phone") if getattr(tts, "phone", False) else key


async def _one(tts: TTSClient, text: str) -> Clip | None:
    try:
        res = await tts.synthesize(text, settings.tts.voice or None)
        audio = b"".join([c async for c in res.chunks])
        return Clip(text, res.mime, audio) if audio else None
    except Exception as e:  # no clip: the turn just starts with the model's own words
        print(f"[ai] filler « {text} » not synthesized ({type(e).__name__}: {e})", flush=True)
        return None


async def warm(tts: TTSClient) -> list[Clip]:
    """Synthesize the phrases once for this voice (in parallel). The mock TTS has no audio: no clips."""
    if tts.name == "mock":
        return []
    async with _lock:
        key = _key(tts)
        if key not in _clips:
            clips = await asyncio.gather(*(_one(tts, p) for p in PHRASES))
            _clips[key] = [c for c in clips if c]
        return _clips[key]


def pick(tts: TTSClient, last: str | None) -> Clip | None:
    """A ready clip, not the one played last. None when warm() has not finished (or failed)."""
    clips = [c for c in _clips.get(_key(tts), []) if c.text != last]
    return random.choice(clips) if clips else None
