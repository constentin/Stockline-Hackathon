"""ElevenLabs TTS, streaming endpoint: POST /v1/text-to-speech/{voice}/stream → mp3 chunks (audio/mpeg).
Phone variant (phone=True): output_format=ulaw_8000, raw µ-law 8 kHz, what Twilio plays as is (no conversion)."""

import httpx

from .base import PHONE_MIME, TTSClient, TTSResult
from .http import body_chunks, http_client, post_stream


class ElevenLabsTTS(TTSClient):
    name = "elevenlabs"

    def __init__(
        self,
        api_key: str,
        model: str = "eleven_flash_v2_5",  # ~100 ms, French OK, cheapest; eleven_v4_turbo = same latency, more expressive
        voice: str = "JBFqnCBsd6RMkjVDRZzb",  # a premade multilingual voice; pick a French one in your ElevenLabs library
        language: str = "fr",
        http: httpx.AsyncClient | None = None,
        phone: bool = False,
    ):
        self.api_key, self.model, self.voice, self.language, self.http = api_key, model, voice, language, http
        self.phone = phone

    def build_body(self, text: str) -> dict:
        body = {"text": text, "model_id": self.model}
        if "multilingual_v2" not in self.model:  # language_code is refused by eleven_multilingual_v2
            body["language_code"] = self.language
        return body

    async def synthesize(self, text: str, voice: str | None = None) -> TTSResult:
        fmt = "ulaw_8000" if self.phone else "mp3_44100_128"
        res = await post_stream(
            self.http or http_client(),
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice or self.voice}/stream?output_format={fmt}",
            who="elevenlabs tts",
            headers={"xi-api-key": self.api_key},
            json=self.build_body(text),
        )
        return TTSResult(PHONE_MIME if self.phone else "audio/mpeg", body_chunks(res))
