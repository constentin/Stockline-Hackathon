"""
Soniox STT over the real-time WebSocket (wss://stt-rt.soniox.com/transcribe-websocket).

Live push-to-talk: the voice session streams the browser's frames here AS THEY ARE RECORDED, so most of the words
are already transcribed when the key is released. At the end of the audio we ask Soniox to FINALIZE: it turns the
last provisional words into final ones right away and marks the spot with a <fin> token — a few hundred ms,
instead of transcribing the whole recording after the release (~4 s).
The browser's WebM/Opus is accepted as is ("audio_format": "auto" detects webm, ogg, wav, mp3…; not mp4).
Phone calls (services/phone.py) send raw µ-law 8 kHz ("audio/x-mulaw"): a raw format has no header to detect, so
it is declared ("audio_format": "mulaw", sample_rate, num_channels).

    → start request {model, audio_format, language_hints, enable_endpoint_detection, context: {terms}}
    → binary audio frames …                       (as they arrive)
    → {"type": "finalize"}                        (end of audio: "make everything final now")
    ← {"tokens": [{"text": " Bon", "is_final": false}, …]}         provisional words: resent whole, may change
    ← {"tokens": [{"text": " Bonjour", "is_final": true}, …]}      final words: sent ONCE, never change
    ← {"tokens": [… {"text": "<fin>", "is_final": true}]}         everything before it is final: done
    ← on error: {"error_code": 401, "error_type": "...", "error_message": "..."} then close

Control tokens: <fin> answers a finalize; <end> marks the end of a sentence when the speaker pauses (endpoint
detection — the hook for hands-free mode, see GUIDE-VOIX.md 4.2). Neither is spoken text.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Callable

from .base import STTClient, STTUpdate
from .http import provider_error
from .ws import chunked, once, open_ws, sending

URL = "wss://stt-rt.soniox.com/transcribe-websocket"
FINALIZE = json.dumps({"type": "finalize"})
MAX_TERMS_CHARS = 6000  # context terms budget, first terms first (Soniox limits the whole context's size)


def audio_format(mime: str) -> dict:
    """Soniox audio settings for a mime type: raw telephone µ-law is declared, everything else is auto-detected."""
    if "mulaw" in mime.lower() or "ulaw" in mime.lower():
        return {"audio_format": "mulaw", "sample_rate": 8000, "num_channels": 1}
    return {"audio_format": "auto"}


def _mean(xs: list[float]) -> float | None:
    """Mean confidence of the words heard (Soniox gives one per token, 0–1)."""
    return round(sum(xs) / len(xs), 3) if xs else None


class SonioxSTT(STTClient):
    name = "soniox"
    streams = True

    def __init__(self, api_key: str, model: str = "stt-rt-v6", language: str = "fr", connect: Callable = open_ws):
        self.api_key, self.model, self.language, self.connect = api_key, model, language, connect

    def start_request(self, hint: str | None, mime: str = "") -> dict:
        req: dict = {
            "model": self.model,
            **audio_format(mime),
            "language_hints": [self.language],
            "enable_endpoint_detection": True,  # final words at each pause, not only at the end
        }
        terms, size = [], 0
        for t in (t.strip() for t in (hint or "").split(",")):
            if t and size + len(t) <= MAX_TERMS_CHARS:
                terms.append(t)
                size += len(t)
        if terms:
            req["context"] = {"terms": terms}  # names the model would otherwise mishear ("Lemoine", "loup")
        return req

    async def transcribe(self, audio: bytes, mime: str, hint: str | None = None) -> str:
        text = ""
        async for update in self.transcribe_stream(once(*chunked(audio)), mime, hint):
            text = update.text
        return text

    async def transcribe_stream(self, audio: AsyncIterator[bytes], mime: str, hint: str | None = None) -> AsyncIterator[STTUpdate]:
        async with self.connect(URL, {"Authorization": f"Bearer {self.api_key}"}, "soniox stt") as ws:
            await ws.send(json.dumps(self.start_request(hint, mime)))
            async with sending(ws, audio, end=FINALIZE):
                final = ""  # final tokens, accumulated (each arrives once)
                confs: list[float] = []  # their confidence
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("error_code"):
                        raise provider_error(
                            "soniox stt", msg["error_code"], f"{msg.get('error_type', '')}: {msg.get('error_message', '')}"
                        )
                    provisional, finalized = "", False  # provisional tokens: the whole current guess, every message
                    for tok in msg.get("tokens") or []:
                        text = tok.get("text") or ""
                        if text == "<fin>":
                            finalized = True
                        elif text.startswith("<"):  # <end>: a pause (endpoint), not a word
                            continue
                        elif tok.get("is_final"):
                            final += text
                            if tok.get("confidence") is not None:
                                confs.append(tok["confidence"])
                        else:
                            provisional += text
                    if finalized or msg.get("finished"):
                        yield STTUpdate(final.strip(), final=True, stable=final.strip(), confidence=_mean(confs))
                        return  # leaving the blocks closes the socket
                    yield STTUpdate((final + provisional).strip(), stable=final.strip())
        raise provider_error("soniox stt", "closed", "connection closed before the final words")

    # ── hands-free: one stream for the whole call ──

    listens = True

    def finalize_message(self) -> str:
        return FINALIZE

    async def listen(
        self, audio: AsyncIterator[bytes], mime: str, hint: str | None, control: "asyncio.Queue[str]"
    ) -> AsyncIterator[STTUpdate]:
        """Continuous transcription: the current sentence as it is heard, final=True at each <end> (a pause Soniox
        detected) or <fin> (answer to a finalize the listener sent after a silence). The stream stays open."""
        async with self.connect(URL, {"Authorization": f"Bearer {self.api_key}"}, "soniox stt") as ws:
            await ws.send(json.dumps(self.start_request(hint, mime)))

            async def relay() -> None:  # control messages (finalize) between the audio frames
                while True:
                    await ws.send(await control.get())

            relaying = asyncio.create_task(relay())
            try:
                async with sending(ws, audio, end=FINALIZE):
                    sentence = ""  # final words of the current sentence
                    confs: list[float] = []
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg.get("error_code"):
                            raise provider_error(
                                "soniox stt", msg["error_code"], f"{msg.get('error_type', '')}: {msg.get('error_message', '')}"
                            )
                        provisional = ""
                        for tok in msg.get("tokens") or []:
                            text = tok.get("text") or ""
                            if text in ("<end>", "<fin>"):  # end of a sentence: everything before it is final
                                if sentence.strip():
                                    yield STTUpdate(sentence.strip(), final=True, stable=sentence.strip(), confidence=_mean(confs))
                                sentence, confs = "", []
                            elif text.startswith("<"):
                                continue
                            elif tok.get("is_final"):
                                sentence += text
                                if tok.get("confidence") is not None:
                                    confs.append(tok["confidence"])
                            else:
                                provisional += text
                        if msg.get("finished"):
                            if sentence.strip():
                                yield STTUpdate(sentence.strip(), final=True, stable=sentence.strip(), confidence=_mean(confs))
                            return
                        yield STTUpdate((sentence + provisional).strip(), stable=sentence.strip())
            finally:
                relaying.cancel()
