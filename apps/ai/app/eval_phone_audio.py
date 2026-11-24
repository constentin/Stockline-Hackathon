"""
Check the phone audio path against the real providers, without Twilio and without touching the ERP:

    python -m app.eval_phone_audio

  1. the phone TTS (µ-law 8 kHz) says a sentence → bytes count and first-byte time;
  2. that µ-law audio is streamed to the STT in hands-free mode, as Twilio would send it (20 ms frames)
     → what the STT heard. Same sentence back = both directions of a phone call work.
"""

import asyncio
import time

from app.clients import PHONE_MIME, get_phone_tts_client, get_stt_client

SENTENCE = "Bonjour, ici l'agent vocal. Il me reste des crevettes à prix cassé, ça vous intéresse ?"
FRAME = 160  # 20 ms of µ-law at 8 kHz: what Twilio sends


async def main() -> None:
    tts, stt = get_phone_tts_client(), get_stt_client()
    t0 = time.perf_counter()
    res = await tts.synthesize(SENTENCE)
    audio, first = b"", None
    async for chunk in res.chunks:
        first = first or time.perf_counter()
        audio += chunk
    print(f"TTS {tts.name} → {res.mime}, {len(audio)} octets ({len(audio) / 8000:.1f} s), 1er octet {int((first - t0) * 1000)} ms")
    assert res.mime == PHONE_MIME and audio, "pas d'audio téléphone"

    async def frames():
        for i in range(0, len(audio), FRAME):
            yield audio[i : i + FRAME]
            await asyncio.sleep(0.02)  # real time, like a call
        for _ in range(50):  # 1 s of silence (µ-law silence = 0xFF)
            yield b"\xff" * FRAME
            await asyncio.sleep(0.02)

    control: asyncio.Queue[str] = asyncio.Queue()
    heard = []
    try:
        async with asyncio.timeout(len(audio) / 8000 + 8):
            async for u in stt.listen(frames(), PHONE_MIME, None, control):
                if u.final:
                    heard.append(u.text)
                    if sum(len(h) for h in heard) > len(SENTENCE) * 0.6:
                        break
    except TimeoutError:
        pass
    print(f"STT {stt.name} a entendu : {' '.join(heard) or '(rien)'}")


if __name__ == "__main__":
    asyncio.run(main())
