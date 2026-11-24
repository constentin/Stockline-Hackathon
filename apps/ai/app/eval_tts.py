"""
Voice bench (ElevenLabs): the same tricky call sentences with several voices and models, to choose by ear.

    uv run python -m app.eval_tts                         (Docker: docker compose exec ai python -m app.eval_tts)
    uv run python -m app.eval_tts --models eleven_flash_v2_5,eleven_v4_turbo --voices 3C1zYzXNXNzrB66ON8rj
    uv run python -m app.eval_tts --list                  (the French voices of your ElevenLabs account)

Each sentence goes through services/speech_text.py first (what the agent really sends). Writes ./tts-bench/: one mp3
per (voice, model, sentence) and index.html to listen side by side (the sounds are embedded in it: it plays on its own), with the time to the first audio byte (TTFB:
what the caller waits for) and the total synthesis time. In Docker, copy it out:
    docker compose cp ai:/app/apps/ai/tts-bench ./tts-bench   then open tts-bench/index.html

Then set TTS_MODEL and TTS_VOICE in .env (make restart). Uses your ElevenLabs credits (~3 000 characters by default).
"""

import asyncio
import base64
import html
import sys
import time
from pathlib import Path

import httpx

from app.clients.elevenlabs import ElevenLabsTTS
from app.config import settings
from app.services.speech_text import for_speech

SENTENCES = [
    "Bonjour Manon, Léa des Parcs de Marennes. Crevettes roses à 15,73 €, moules de bouchot à 2,98 €, bulots à 9,20 €.",
    "Dix kilos de crevettes roses, 157,30 € HT, livrés mercredi 25 novembre. Je valide ?",
    "Les fines de claire n°3 se gardent jusqu'à vendredi ; je peux vous faire -25 % sur le tourteau.",
    "Pas de souci, je vous rappelle demain vers 15h30. Bonne journée à toute l'équipe de la Brasserie Lambert !",
]
VOICES = {  # French female voices of the team's ElevenLabs account (see --list)
    "3C1zYzXNXNzrB66ON8rj": "Jade (service client)",
    "YxrwjAKoUKULGd0g8K9Y": "Lucie (service client)",
    "fBpCO0Kf0krKLYGOu65w": "Émilie (conseillère)",
    "KbGc4PHQNObvmeinlID4": "Comptoir femme",
}
MODELS = ["eleven_flash_v2_5", "eleven_v4_turbo", "eleven_v4"]


def b64(path: Path) -> str:
    """The mp3 inside the page itself: it then plays wherever index.html is opened (WSL folder, editor preview,
    a copy sent to a teammate), without its mp3 files next to it."""
    return base64.b64encode(path.read_bytes()).decode()


def arg(name: str) -> list[str] | None:
    return sys.argv[sys.argv.index(name) + 1].split(",") if name in sys.argv else None


async def list_voices() -> None:
    async with httpx.AsyncClient(timeout=20) as h:
        res = await h.get("https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": settings.tts.api_key})
        for v in res.json().get("voices", []):
            labels = v.get("labels") or {}
            if labels.get("language") == "fr" or "fr" in (v.get("name") or "").lower():
                print(f"{v['voice_id']}  {v['name']}  ({labels.get('gender')}, {labels.get('accent')})")


async def synth(model: str, voice: str, text: str, out: Path) -> tuple[int | None, int | None, str | None]:
    tts = ElevenLabsTTS(settings.tts.api_key, model, voice, settings.tts.language)
    t0 = time.perf_counter()
    first, audio = None, b""
    try:
        res = await tts.synthesize(text)
        async for chunk in res.chunks:
            first = first or int((time.perf_counter() - t0) * 1000)
            audio += chunk
    except Exception as e:
        return None, None, str(e)[:160]
    out.write_bytes(audio)
    return first, int((time.perf_counter() - t0) * 1000), None


async def main() -> None:
    if "--list" in sys.argv:
        await list_voices()
        return
    if settings.tts.provider != "elevenlabs" or not settings.tts.api_key:
        print("TTS_PROVIDER=elevenlabs et TTS_API_KEY requis")
        return
    models, voices = arg("--models") or MODELS, arg("--voices") or list(VOICES)
    root = Path.cwd() / "tts-bench"
    root.mkdir(exist_ok=True)
    rows = []
    for model in models:
        for voice in voices:
            for i, sentence in enumerate(SENTENCES):
                name = f"{model}__{voice}__{i + 1}.mp3"
                first, total, err = await synth(model, voice, for_speech(sentence), root / name)
                rows.append((model, voice, i, name, first, total, err))
                print(f"{model:<20} {VOICES.get(voice, voice):<24} phrase {i + 1}: "
                      f"{err or f'1er octet {first} ms, total {total} ms'}")  # fmt: skip
    # summary: median TTFB per model
    print()
    for model in models:
        ttfb = sorted(r[4] for r in rows if r[0] == model and r[4] is not None)
        print(f"{model:<20} 1er octet médian {ttfb[len(ttfb) // 2] if ttfb else '—'} ms sur {len(ttfb)} phrases")
    page = ["<!doctype html><meta charset=utf-8><title>Banc de voix</title>",
            "<style>body{font:14px system-ui;margin:24px}td,th{padding:6px 10px;border-bottom:1px solid #ddd;vertical-align:top}"
            "audio{width:220px}small{color:#777}</style><h1>Banc de voix ElevenLabs</h1>"]  # fmt: skip
    for i, sentence in enumerate(SENTENCES):
        page.append(f"<h3>Phrase {i + 1}</h3><p>{html.escape(sentence)}<br><small>envoyé : {html.escape(for_speech(sentence))}</small></p>")
        page.append("<table><tr><th>voix</th>" + "".join(f"<th>{m}</th>" for m in models) + "</tr>")
        for voice in voices:
            cells = []
            for model in models:
                r = next(r for r in rows if r[0] == model and r[1] == voice and r[2] == i)
                cells.append(f"<td><small>{html.escape(r[6])}</small></td>" if r[6] else
                             f"<td><audio controls preload=auto src='data:audio/mpeg;base64,{b64(root / r[3])}'></audio>"
                             f"<br><small>1er octet {r[4]} ms</small></td>")  # fmt: skip
            page.append(f"<tr><td>{html.escape(VOICES.get(voice, voice))}<br><small>{voice}</small></td>{''.join(cells)}</tr>")
        page.append("</table>")
    (root / "index.html").write_text("\n".join(page), encoding="utf-8")
    print(f"\nà écouter : {root / 'index.html'}")


if __name__ == "__main__":
    asyncio.run(main())
