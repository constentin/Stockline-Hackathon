"""
Pre-recorded waiting phrases, so the caller never waits in silence while the agent works.

    level 1  at once, when the model will work before speaking   « Je regarde les stocks. »
    level 2  if it is still working ~1.8 s later                  « Je regarde si je peux faire une réduction de ce type. »
    level 3  if it lasts (~4.5 s, then ~9 s)                      « Merci de patienter, je vérifie les quantités… »

The phrase fits what the agent is doing: the TOPIC comes from what Jev understood (the client chooses → stock,
negotiates → price…) or from the tool the model just called (accept_offer → stock, quote_offer → price…).
The escalation itself is in services/voice_session.py (Patience).

They are synthesized ONCE per voice (warm(), when a session opens; short ones first) and kept in memory: playing
one costs no TTS call and no latency. pick() avoids the phrases heard recently in the call. The model is told not
to write a waiting phrase itself (agent/prompts.py): the switchboard does it.

To add phrases: put them in PHRASES under the right topic and level (level 1: 1–4 words; level 2: one short
sentence; level 3: a longer, reassuring sentence). Never promise an outcome (« je peux vous faire… »): the agent
does not know yet.
"""

import asyncio
import random
from dataclasses import dataclass

from app.clients import TTSClient
from app.config import settings

PHRASES: dict[str, dict[int, list[str]]] = {
    "general": {
        1: ["Je regarde.", "Un instant.", "Je vérifie.", "Une seconde.", "Je m'en occupe.", "Voyons ça."],
        2: [
            "Je regarde ça tout de suite.",
            "Laissez-moi vérifier une petite chose.",
            "Je vérifie ça pour vous, une seconde.",
            "Patientez un instant, je regarde.",
            "Je jette un œil, ce ne sera pas long.",
            "Je regarde ça de près.",
        ],
        3: [
            "Merci de patienter, je vérifie tout ça pour vous et je reviens vers vous dans un instant.",
            "Ça prend une petite seconde de plus, je m'assure que tout est juste avant de vous répondre.",
            "Je suis toujours là, je termine la vérification et je vous dis ça tout de suite.",
            "Désolée pour l'attente, j'y suis presque, encore un petit instant.",
        ],
    },
    "stock": {
        1: ["Je regarde les stocks.", "Je vérifie le stock.", "Je regarde ce qu'il reste."],
        2: [
            "Je regarde dans les stocks.",
            "Je vérifie ce qu'il nous reste en chambre froide.",
            "Je regarde les quantités disponibles.",
            "Je vérifie qu'on a bien tout ça en stock.",
            "Je regarde ce que je peux vous mettre de côté.",
        ],
        3: [
            "Je vérifie les quantités disponibles et les dates des lots, pour vous donner la bonne information.",
            "Je regarde le stock lot par lot, pour être sûre de pouvoir vous livrer tout ça.",
            "Je vérifie qu'on a assez de marchandise pour votre commande, je reviens vers vous tout de suite.",
        ],
    },
    "price": {
        1: ["Je regarde le prix.", "Je calcule.", "Je regarde ça."],
        2: [
            "Je regarde si je peux faire une réduction de ce type.",
            "Je regarde ce que je peux vous proposer.",
            "Je calcule le prix avec la remise.",
            "Je vérifie jusqu'où je peux descendre.",
            "Je regarde si on a un peu de marge sur ce lot.",
        ],
        3: [
            "Je regarde avec la remise ce que je peux vous faire de mieux, ça me prend une petite seconde.",
            "Je fais le calcul pour vous proposer le meilleur prix possible, patientez un instant.",
            "Je vérifie les conditions de prix sur ce lot pour voir si on peut faire un geste.",
        ],
    },
    "order": {
        1: ["Je note.", "C'est parti.", "Je prépare ça."],
        2: [
            "Je prépare votre commande.",
            "J'enregistre ça tout de suite.",
            "Je mets ça sur votre commande.",
            "Je valide la commande de mon côté.",
        ],
        3: [
            "J'enregistre la commande et je vérifie la tournée de livraison, encore un petit instant.",
            "Je finalise la commande dans le système, je vous confirme ça dans quelques secondes.",
            "Je prépare le bon de commande et je vérifie le jour de livraison, j'ai presque fini.",
        ],
    },
    "info": {
        1: ["Je regarde.", "Bonne question.", "Je vérifie."],
        2: [
            "Je regarde dans votre dossier.",
            "Je vérifie l'information pour vous.",
            "Je regarde les dates sur ce lot.",
            "Je retrouve ça, une seconde.",
        ],
        3: [
            "Je cherche l'information exacte dans votre dossier, je préfère vous donner la bonne réponse.",
            "Je vérifie dans le détail pour ne pas vous dire de bêtise, encore un petit instant.",
        ],
    },
    "callback": {
        1: ["Je note.", "C'est noté."],
        2: ["Je note ça dans votre dossier.", "Je bloque ce créneau pour vous rappeler.", "Je programme le rappel."],
        3: ["J'enregistre le rappel dans votre dossier, pour qu'on vous recontacte au bon moment."],
    },
}

# What the agent is doing → topic of the phrase
TOOL_TOPIC = {
    "accept_offer": "stock", "get_product_stock": "stock", "find_alternatives": "stock", "search": "stock",
    "quote_offer": "price", "get_price": "price",
    "confirm_call_order": "order", "confirm_order": "order", "create_draft_order": "order", "add_order_line": "order",
    "get_order": "order",
    "get_call_brief": "info", "get_client": "info", "get_client_history": "info", "search_context": "info",
    "get_context": "info",
    "record_call_outcome": "callback", "log_client_event": "callback", "save_context": "callback",
}  # fmt: skip
INTENT_TOPIC = {"choisit": "stock", "negocie": "price", "valide": "order", "question": "info", "rappel": "callback"}

RECENT = 8  # a phrase heard among the last RECENT ones of the call is not picked again (unless nothing else is left)


@dataclass(frozen=True)
class Clip:
    text: str
    mime: str
    audio: bytes
    topic: str = "general"
    level: int = 1

    @property
    def seconds(self) -> float:
        """Playing time: mp3 at 128 kb/s (browser) or µ-law 8 kHz (phone)."""
        return len(self.audio) / (8000 if "basic" in self.mime or "ulaw" in self.mime or "mulaw" in self.mime else 16000)


_clips: dict[tuple[str, ...], list[Clip]] = {}  # (provider, model, voice[, "phone"]) → clips
_warming: dict[tuple[str, ...], asyncio.Task] = {}
_limit = asyncio.Semaphore(4)  # TTS providers cap concurrent requests


def _key(tts: TTSClient) -> tuple[str, ...]:
    """One set of clips per voice AND audio format: the phone (µ-law) clips are not the browser's (mp3) ones."""
    key = (tts.name, getattr(tts, "model", ""), settings.tts.voice or getattr(tts, "voice", ""))
    return (*key, "phone") if getattr(tts, "phone", False) else key


async def _one(tts: TTSClient, text: str, topic: str, level: int) -> Clip | None:
    async with _limit:
        try:
            res = await tts.synthesize(text, settings.tts.voice or None)
            audio = b"".join([c async for c in res.chunks])
            return Clip(text, res.mime, audio, topic, level) if audio else None
        except Exception as e:  # no clip: another phrase (or the model's own words) will do
            print(f"[ai] filler « {text} » not synthesized ({type(e).__name__}: {e})", flush=True)
            return None


async def _warm(tts: TTSClient, key: tuple[str, ...]) -> None:
    store = _clips.setdefault(key, [])
    for level in (1, 2, 3):  # short ones first: they are needed first
        jobs = [_one(tts, p, topic, level) for topic, levels in PHRASES.items() for p in levels.get(level, [])]
        store.extend(c for c in await asyncio.gather(*jobs) if c)


async def warm(tts: TTSClient) -> None:
    """Synthesize all the phrases once for this voice, in the background-friendly order (level 1 first).
    Calling it again while it runs (or after) costs nothing. The mock TTS has no audio: no clips."""
    if tts.name == "mock":
        return
    key = _key(tts)
    if key not in _warming:
        _warming[key] = asyncio.create_task(_warm(tts, key))
    await asyncio.shield(_warming[key])


async def warm_all() -> None:
    """At service start: the phrases for the browser voice, then for the phone voice (Twilio, if configured)."""
    from app.clients import get_phone_tts_client, get_tts_client

    targets = [get_tts_client()]
    try:
        targets.append(get_phone_tts_client())
    except Exception:  # no phone TTS for this provider (mock, openai): browser only
        pass
    for tts in targets:
        if tts.name == "mock":
            continue
        t0 = asyncio.get_running_loop().time()
        await warm(tts)
        n = len(_clips.get(_key(tts), []))
        total = sum(len(v) for levels in PHRASES.values() for v in levels.values())
        where = "téléphone" if getattr(tts, "phone", False) else "navigateur"
        print(f"[ai] phrases d'attente prêtes ({where}) : {n}/{total} en {asyncio.get_running_loop().time() - t0:.1f} s", flush=True)


def pick(tts: TTSClient, topic: str = "general", level: int = 1, recent: list[str] | None = None) -> Clip | None:
    """A ready phrase of this level about this topic (else a general one), not heard recently in the call.
    None while nothing is synthesized yet."""
    clips = _clips.get(_key(tts), [])
    recent = recent or []
    for pool in (
        [c for c in clips if c.level == level and c.topic == topic and c.text not in recent],
        [c for c in clips if c.level == level and c.topic == "general" and c.text not in recent],
        [c for c in clips if c.level == level and c.text not in recent[-1:]],
    ):
        if pool:
            return random.choice(pool)
    return None
