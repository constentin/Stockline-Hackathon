"""
Jev router for outbound calls: ONE Jev request (~0.15–0.25 s) answers three typed questions about what the client
just said, before (or instead of) the big model:

    intent    what he wants: validate the recap, choose products, negotiate, refuse, be called back…
    offer     which product of the call brief he is talking about (or none / several)
    complete  is his sentence finished? (hands-free mode: answer now, or wait for the rest)
    addressee is he talking to the agent, or to someone next to him (« Kevin, sors les poubelles ! »)?
    clarity   can his sentence be understood at all (« Le chat des dés vingt pour la cave » → no: ask, don't guess)

It is started on the PARTIAL transcript while the client is still speaking (voice_session.py), so at the end of
his sentence the answer is usually already there: the routing costs no latency. What is done with it is in
services/outbound.py (plan_turn). No JEV_API_KEY, Jev down or too slow → None, and the turn goes to the big model
exactly as before.
"""

import asyncio
import re
import time
from dataclasses import dataclass

from app.clients import get_jev_client
from app.clients.jev import choice_question, yes_no_question

INTENTS = {
    "valide": "dit oui pour valider la commande que l'agent vient de récapituler (oui, c'est bon, allez-y, validez)",
    "choisit": "s'intéresse à un ou plusieurs produits, ou donne des quantités",
    "negocie": "trouve ça trop cher ou demande un meilleur prix",
    "question": "pose une question (date, origine, prix habituel, livraison…)",
    "refuse": "ne veut rien de ce qui est proposé",
    "rappel": "demande à être rappelé plus tard, n'a pas le temps maintenant",
    "ne_plus_appeler": "ne veut plus être appelé ni démarché",
    "robot": "demande s'il parle à un robot, une IA ou une vraie personne",
    "au_revoir": "termine l'appel : merci, c'est tout, au revoir",
    "autre": "autre chose, ou on ne sait pas",
}
NONE_OFFER = "aucun"
TIMEOUT_S = 1.2  # a routing answer later than this is not worth waiting for


@dataclass(frozen=True)
class Route:
    text: str  # the client words it was computed on
    intent: str
    confidence: float
    offer: str | None  # offerId he talks about, None = none / several
    offer_confidence: float
    complete: float  # probability that his sentence is finished
    ms: int
    addressed: float = 1.0  # probability that he was talking to the agent (not to a colleague next to him)
    clear: float = 1.0  # probability that his sentence can be understood (a garbled transcript scores low)


def same_words(a: str, b: str) -> bool:
    """Same words, ignoring case and punctuation (a partial transcript vs the final one)."""
    w = lambda s: re.findall(r"[a-zà-ÿ0-9']+", s.lower())  # noqa: E731
    return w(a) == w(b)


async def classify(text: str, agent_said: str, offers: list[dict]) -> Route | None:
    """offers: [{"offerId", "name"}] (short names). None when Jev is not configured, fails or is too slow."""
    jev = get_jev_client()
    if not jev or not text.strip():
        return None
    names = {o["offerId"]: o["name"] for o in offers}
    questions = {
        "intent": choice_question("Au téléphone, que veut le client d'après sa dernière phrase ?", INTENTS),
        "offer": choice_question(
            "De quel produit le client parle-t-il ?", {**names, NONE_OFFER: "aucun produit précis, ou plusieurs produits"}
        ),
        "addressee": choice_question(
            "Un restaurateur est au téléphone avec une commerciale qui lui propose des produits. À qui s'adresse sa "
            "dernière phrase ?",
            {
                "agent": "à la commerciale au téléphone : il répond à ce qu'elle vient de dire, pose une question sur ses "
                "produits, leurs prix, quantités, dates ou la livraison, accepte, refuse, dit au revoir ou lui demande "
                "de patienter",
                "autre": "à quelqu'un près de lui dans le restaurant : un collègue, le chef, un serveur ; il parle du "
                "service, des tables, de la cuisine, demande de poser, ranger, préparer quelque chose, ou appelle "
                "quelqu'un par son prénom",
            },
        ),
        "clarity": choice_question(
            "Au téléphone, une commerciale propose des produits à un restaurateur. Peut-elle comprendre ce que veut le "
            "client d'après sa dernière phrase (transcription automatique, qui peut contenir des erreurs) ?",
            {
                "clair": "la phrase a un sens dans la conversation : une réponse, un choix de produit, une quantité, une "
                "question, un oui, un non, un au revoir",
                "pas_clair": "la phrase est incompréhensible ou incohérente : mots sans rapport avec la conversation, "
                "transcription abîmée, phrase coupée, produit ou quantité impossibles à identifier",
            },
        ),
        "complete": yes_no_question(
            "Le client a-t-il fini sa phrase ?",
            yes="phrase complète : une réponse, une question, un oui, un non, une demande, un au revoir",
            no="phrase coupée au milieu : elle finit par « mais », « et », « euh », « je voudrais », « parce que »",
        ),
    }
    state = {"agent": agent_said, "client": text, "produits proposés": list(names.values())}
    t0 = time.perf_counter()
    try:
        res = await asyncio.wait_for(jev.ask(state, questions), TIMEOUT_S)
        a = res["answers"]
        offer = a["offer"]["choice"]
        return Route(
            text=text,
            intent=a["intent"]["choice"],
            confidence=a["intent"]["confidence"],
            offer=None if offer == NONE_OFFER else offer,
            offer_confidence=a["offer"]["confidence"],
            complete=a["complete"]["noul"],
            ms=int((time.perf_counter() - t0) * 1000),
            addressed=a["addressee"]["probabilities"].get("agent", 1.0),
            clear=a["clarity"]["probabilities"].get("clair", 1.0),
        )
    except Exception as e:  # timeout, network, unexpected shape: no routing, the big model handles the turn
        print(f"[ai] Jev routing skipped ({type(e).__name__}: {e})", flush=True)
        return None
