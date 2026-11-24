"""
The agent's FIRST reply, said at once while the big model works: two real replies instead of one.

    client : « Vous pouvez faire mieux sur 40 kilos de moules ? »
    agent  : « Quarante kilos de moules, je regarde ce que je peux faire sur le prix. »    ← first_reply(), < 1 s
             … the big model calls quote_offer, thinks …
    agent  : « Je peux vous les faire à 2 euros 79 le kilo, soit 111 euros 60. Ça vous va ? »   ← the real answer

The small model (LLM_FAST_MODEL, Haiku) writes it from a TINY prompt (no company context, no tools, no history but
the last exchange): it answers in a few hundred ms. It may not state any fact (price, stock, date, yes/no): it does
not know them yet; it only shows it understood and says what it is checking. When there is nothing to check
(greeting, thanks, plain yes), it answers NOTHING and the big model's answer comes alone.

The big model is told (a switchboard note on the client's words) that an acknowledgement was already said, so it
starts directly with the result. See services/voice_session.py (run_turn) for the timing and the ordering.
"""

import re
from collections.abc import AsyncIterator

from app.clients import get_fast_llm_client
from app.clients.base import LLMRequest, LLMText
from app.schemas import ChatMessage

NOTHING = "RIEN"
SENTENCE_END = re.compile(r"[.!?…]")
MAYBE_NOTHING = re.compile(r"(r(i(e(n[\s.!…]*)?)?)?)?", re.I)  # "", "R", "RI"… "RIEN.": not a reply (yet)
NOTE = (
    "\n[Standard : le client a déjà entendu ta courte réponse d'attente (tu as reformulé sa demande et dit ce que tu "
    "vérifies). Ta réponse donne directement le résultat, sans la reformuler ni redire « je regarde ».]"
)

SYSTEM = f"""Tu es {{agent}}, au téléphone avec un client professionnel d'un grossiste alimentaire.
Le client vient de parler. Pendant que tu vérifies dans l'ERP, tu lui réponds TOUT DE SUITE par UNE phrase courte
(5 à 14 mots), naturelle, à l'oral, qui montre que tu as compris sa demande précise et dit ce que tu vérifies ou
prépares. Tu peux reprendre ce qu'il a dit (produit, quantité qu'il demande).

Interdit, car tu ne connais pas encore la réponse : tout prix, quantité disponible, total, date ou délai ; tout accord
ou refus ; toute promesse (« c'est possible », « je peux vous faire ») ; les questions ; les salutations ; le markdown.
Une demande de prix, de remise, de geste, de quantité, de stock, de date ou de livraison demande TOUJOURS une
vérification. Seulement si sa phrase n'en demande aucune (bonjour, merci, au revoir, simple oui ou non, il
hésite sans rien demander), réponds exactement : {NOTHING}

Exemples du ton (ne les recopie pas) :
- « Quarante kilos de moules, je regarde ce que je peux faire sur le prix. »
- « C'est entendu, je vois si je peux faire un effort sur les moules. »
- « Je vérifie qu'on a bien assez de bulots pour mercredi. »
- « Je regarde jusqu'à quand se gardent les fines de claire. »
- « D'accord, je prépare ça avec les crevettes et les moules. »"""


def build_request(heard: str, agent_said: str, products: list[str], recent: list[str], agent: str) -> LLMRequest:
    lines = []
    if agent_said:
        lines.append(f"Tu viens de dire : « {agent_said[-400:]} »")
    lines.append(f"Le client répond : « {heard} »")
    if products:
        lines.append(f"Produits dont on parle dans l'appel : {', '.join(products)}.")
    if recent:
        lines.append(f"Ne commence pas comme tes réponses d'attente récentes : {' / '.join(recent[-4:])}")
    return LLMRequest(
        system=SYSTEM.format(agent=agent), messages=[ChatMessage(role="user", content="\n".join(lines))], max_tokens=60, no_thinking=True
    )


async def first_reply(
    heard: str, agent_said: str = "", products: list[str] | None = None, recent: list[str] | None = None, agent: str = "l'assistante"
) -> AsyncIterator[str]:
    """The immediate reply, streamed, ONE sentence (anything after its end is dropped: small models sometimes add a
    comment). Yields nothing when there is nothing to check (the model returns NOTHING)."""
    req = build_request(heard, agent_said, products or [], recent or [], agent)
    text = ""  # what was yielded so far
    pending = ""  # held until we know it is not NOTHING
    async for ev in get_fast_llm_client().stream(req):
        if not isinstance(ev, LLMText):
            continue
        chunk = ev.delta.replace("«", "").replace("»", "").replace("\"", "")
        if not text:
            pending += chunk
            if MAYBE_NOTHING.fullmatch(pending.strip()):
                continue  # could still be exactly NOTHING: wait for a few more characters
            chunk, pending = pending.lstrip(), ""
        end = SENTENCE_END.search(chunk)
        if end:  # the sentence is over: say it, ignore the rest
            yield chunk[: end.end()]
            return
        text += chunk
        yield chunk
