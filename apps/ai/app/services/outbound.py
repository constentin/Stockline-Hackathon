"""
Outbound clearance calls: what the voice session (and the CLI, the bench) need to run one call.

    start_call(call_id)  → the call brief (ERP), call marked in_progress, the prompt block built from it
    OPENING_CUE          → the bracketed "user" message that makes the agent speak first
    plan_turn(...)       → from the Jev route (services/router.py): which model answers, and for the clear-cut cases
                           (yes to the recap, refusal, "don't call me again") the ERP action done by code right away
    run_outbound(...)    → the agent turn, with a fallback (model error or silence → the other model, then an excuse)
    end_call(...)        → when the caller hangs up and nothing was recorded: classify and record the outcome

The brief goes at the END of the system prompt (run_agent(system_extra=…)): the rest of the prompt stays identical
from one call to the next, so the prompt cache keeps serving it.
"""

import asyncio
import json
import random
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass

from app.agent import run_agent
from app.agent.prompts import french_date
from app.clients import LLMClient, get_fast_llm_client, get_llm_client
from app.schemas import ChatMessage, ChatStreamEvent, ErrorEvent, TextEvent, ToolCallEvent
from app.tools import ErpClient, outbound_tools
from app.tools.erp import ErpError, enc

from .router import Route
from app.config import settings

NOTE_SEP = "\n[Standard"  # a switchboard note appended to the client's words (plan_turn)
OPENING_CUE = "[Standard : appel sortant, le client vient de décrocher. À toi de parler.]"
HANGUP_CUE = "[Standard : le client a raccroché.]"

OUTCOMES = ("accepted", "declined", "callback", "no_answer", "do_not_call")

# A different way to open each call (start_call picks one, never one of the last two used): the same sentence on
# every call sounds robotic. The model follows the style in its own words; the constraints (all offers with their
# price, short, an open question) stay in agent/prompts.py.
OPENING_STYLES = [
    "Chaleureuse et directe : salue-le par son prénom, présente-toi, dis que tu as pensé à lui pour quelques produits "
    "à date courte que tu fais à prix cassé, cite-les avec leur prix, et demande ce qui lui ferait plaisir.",
    "Une question d'abord : salue-le, présente-toi, demande-lui s'il a deux minutes ou s'il est en plein coup de feu, "
    "puis enchaîne tout de suite sur les offres et leur prix, et laisse-le choisir.",
    "Ses habitudes : salue-le, présente-toi, rappelle en quelques mots ce qu'il prend souvent (d'après la fiche), puis "
    "présente les offres à petit prix et demande ce qui l'intéresse.",
    "Bonne nouvelle : salue-le, annonce que tu as une bonne affaire pour lui aujourd'hui, présente-toi en passant, cite "
    "les offres avec leur prix (date courte, donc prix bas) et demande si ça lui dit.",
    "Complice : salue-le, présente-toi, dis que tu appelles tes meilleurs clients en premier, cite les offres avec leur "
    "prix et demande ce qu'il en pense.",
    "Concrète et rapide : salue-le, présente-toi en trois mots, va droit au but avec les offres et leurs prix, précise "
    "qu'il faut les écouler vite, et demande lesquelles il veut.",
    "Le service qui vient : salue-le, présente-toi, fais un petit lien avec son service ou son week-end à venir, puis "
    "présente les offres à petit prix et demande ce qui l'arrangerait.",
]
CALLBACK_OPENING = (
    "C'est un RAPPEL (previousCalls dans la fiche : ce qui s'est dit la dernière fois) : salue-le, dis que tu le "
    "rappelles comme convenu, résume en une phrase où vous en étiez (par exemple la commande qu'il allait valider, les "
    "produits qui l'intéressaient) et pose la question qui fait avancer. Ne refais pas la présentation des offres."
)
# How the call starts and how the agent introduces itself: drawn too, so the very first words change from call to call
GREETINGS = [
    "Bonjour {prenom} !",
    "Allô, {prenom} ? Bonjour !",
    "Bonjour {prenom}, comment allez-vous ?",
    "{prenom}, bonjour !",
    "Bonjour {prenom}, j'espère que la journée se passe bien.",
    "Bonjour {prenom}, je ne vous dérange pas trop ?",
]
INTRODUCTIONS = [  # + the company, said by the agent (each team has its own)
    "c'est {agent}",
    "{agent} à l'appareil",
    "ici {agent}",
    "c'est {agent} qui vous appelle",
    "je suis {agent}",
]
_recent_openings: list[int] = []
_recent_greetings: list[int] = []


def pick_opening(brief: dict) -> str:
    """The way to open this call: a callback picks the conversation up; otherwise a style not used in the last 2 calls."""
    if brief.get("previousCalls"):
        return CALLBACK_OPENING
    choices = [i for i in range(len(OPENING_STYLES)) if i not in _recent_openings] or list(range(len(OPENING_STYLES)))
    i = random.choice(choices)
    _recent_openings[:] = [*_recent_openings, i][-2:]
    g = random.choice([k for k in range(len(GREETINGS)) if k not in _recent_greetings] or list(range(len(GREETINGS))))
    _recent_greetings[:] = [*_recent_greetings, g][-3:]
    first = (((brief.get("client") or {}).get("contact") or {}).get("name") or "").split(" ")[0] or "Monsieur"
    from app.agent.prompts import OUTBOUND_AGENT_NAME

    greeting = GREETINGS[g].format(prenom=first)
    intro = random.choice(INTRODUCTIONS).format(agent=OUTBOUND_AGENT_NAME)
    return f"Commence exactement par « {greeting} », présente-toi ainsi : « {intro} » suivi du nom de l'entreprise. Puis : {OPENING_STYLES[i]}"


@dataclass
class OutboundCall:
    call_id: str
    brief: dict

    @property
    def prompt(self) -> str:
        return f"# Appel en cours\n\n{json.dumps(self.brief, ensure_ascii=False, separators=(',', ':'))}"

    @property
    def client_name(self) -> str:
        return self.brief.get("client", {}).get("name", "")

    @property
    def offers(self) -> list[dict]:
        """[{offerId, name}] with the short spoken names (for Jev and the STT)."""
        return [{"offerId": o["offerId"], "name": short_name(o.get("productName", ""))} for o in self.brief.get("offers", [])]

    def stt_terms(self) -> str:
        """This call's words for the STT, first: the products on offer (full and short names), the contact, the client."""
        contact = ((self.brief.get("client") or {}).get("contact") or {}).get("name") or ""
        names = [o.get("productName", "") for o in self.brief.get("offers", [])] + [o["name"] for o in self.offers]
        return ", ".join(t for t in [*names, contact, self.client_name] if t)


def short_name(product_name: str) -> str:
    """"Crevette rose cuite 30/40 (colis 5 kg)" → "Crevette rose cuite": what people say on the phone."""
    return re.split(r"\s+[\d(–]", product_name, maxsplit=1)[0].strip() or product_name


async def start_call(call_id: str, actor_name: str | None = None) -> OutboundCall:
    """Load the brief and mark the call in progress (one more attempt). Raises ErpError if the ERP refuses."""
    erp = ErpClient("voice_agent", actor_name)
    await erp.patch(f"/api/campaign-calls/{enc(call_id)}", {"status": "in_progress"})
    brief = with_spoken_dates(await erp.get(f"/api/campaign-calls/{enc(call_id)}/brief"))
    brief["openingStyle"] = pick_opening(brief)
    return OutboundCall(call_id=call_id, brief=brief)


def with_spoken_dates(brief: dict) -> dict:
    """Add the dates in words: models get the weekday of an ISO date wrong ("jusqu'à jeudi" for a Friday)."""

    def words(iso: str | None) -> str | None:
        return french_date(iso).rsplit(" ", 1)[0] if iso else None  # "vendredi 27 novembre"

    return {
        **brief,
        "deliveryOn": words(brief.get("deliveryDate")),
        "offers": [{**o, "keepsUntil": words(o.get("expiresAt"))} for o in brief.get("offers", [])],
    }


def transcript(history: list[ChatMessage]) -> str:
    """The spoken exchange only (no tool traffic, no cues), for classifying the outcome."""
    lines = []
    for m in history:
        if m.role not in ("user", "assistant") or not m.content or m.content.startswith("[Standard"):
            continue
        said = m.content.split(NOTE_SEP)[0]  # without the switchboard note the router may have added
        if said.startswith("[") and "]\n" in said:  # « [Avant, le client a dit à quelqu'un près de lui : …] »
            said = said.split("]\n", 1)[1]
        lines.append(f"{'Client' if m.role == 'user' else 'Agent'} : {said}")
    return "\n".join(lines)


def recorded_outcome(history: list[ChatMessage]) -> bool:
    """True when the agent already called record_call_outcome during the call."""
    closing = ("record_call_outcome", "confirm_call_order")
    return any(m.role == "assistant" and any(c.name in closing for c in m.tool_calls or []) for m in history)


async def guess_outcome(history: list[ChatMessage]) -> tuple[str, str]:
    """(outcome, summary) from what was said and done, without the LLM: Jev when available, else simple rules."""
    accepted = any(
        m.role == "assistant" and any(c.name in ("accept_offer", "confirm_call_order") for c in m.tool_calls or []) for m in history
    )
    said = transcript(history)
    if accepted:
        return "accepted", "Offre acceptée (résultat enregistré au raccrocher)"
    if not any(line.startswith("Client") for line in said.splitlines()):
        return "no_answer", "Le client a raccroché sans parler"
    from app.clients import get_jev_client  # imported here: optional dependency (JEV_API_KEY)

    jev = get_jev_client()
    if jev:
        try:
            d = await jev.choose(
                said[-3000:],
                "Comment s'est terminé cet appel commercial ?",
                {
                    "declined": "le client a refusé l'offre",
                    "callback": "le client a demandé à être rappelé plus tard",
                    "do_not_call": "le client ne veut plus être appelé",
                },
            )
            if d.confidence >= 0.6:
                return d.choice, "Résultat déduit au raccrocher"
        except Exception:  # Jev down: fall back to the rule below
            pass
    return "declined", "Le client a raccroché sans prendre d'offre"


async def end_call(call: OutboundCall, history: list[ChatMessage], actor_name: str | None = None) -> str | None:
    """Called when the caller hangs up. Records an outcome unless the agent already did, then keeps what was said
    (the brief of a callback gives it back: the agent picks the conversation up). Returns the outcome written."""
    erp = ErpClient("voice_agent", actor_name)
    outcome = await _record_outcome(erp, call, history)
    lines = transcript(history).splitlines()
    if any(line.startswith("Client") for line in lines):  # the client never spoke (no answer): nothing to pick up
        try:
            await erp.post(f"/api/campaign-calls/{enc(call.call_id)}/transcript", {"lines": lines})
        except ErpError as e:  # the outcome is what matters: a lost transcript only makes a callback less smooth
            print(f"[ai] conversation non enregistrée ({e})", flush=True)
    return outcome


async def _record_outcome(erp: ErpClient, call: OutboundCall, history: list[ChatMessage]) -> str | None:
    if recorded_outcome(history):
        return None
    current = await erp.get(f"/api/campaign-calls/{enc(call.call_id)}")
    if current.get("status") not in ("pending", "in_progress"):
        return None  # already closed (by the router, the agent or the backend): never overwrite an outcome
    outcome, summary = await guess_outcome(history)
    body = {"status": outcome, "summary": summary}
    if outcome == "callback":
        body["callbackAt"] = call.brief.get("deliveryDate", "") + "T10:00"  # no time said: the next delivery day, 10 h
    await erp.patch(f"/api/campaign-calls/{enc(call.call_id)}", body)
    return outcome


# ───────── One turn: Jev route → plan → agent (with fallback) ─────────

SURE = 0.8  # Jev confidence above which the code acts by itself (the bench checks these cases)
HINT = 0.8  # product hint for the big model
FIRST_EVENT_TIMEOUT_S = 4.0  # no word and no tool call from the model after this: try the other model
SORRY = "Pardon, la ligne a coupé un instant. Vous pouvez répéter ?"
# Asking again without sounding like a broken record: what to do on the 1st, 2nd, 3rd unclear sentence in a row.
CLARIFY_STEPS = [
    "Ne devine pas. Demande-lui de préciser en UNE phrase courte et naturelle, en reprenant ce que tu as compris s'il y "
    "a quelque chose (un produit, une quantité : « Vingt kilos de quoi, pardon ? »), sinon demande-lui simplement de "
    "répéter (« Pardon, je vous ai mal entendu. »).",
    "C'est la deuxième fois que tu ne comprends pas. Ne redemande pas de répéter de la même façon : propose-lui un "
    "choix simple qu'il peut trancher en un mot (« Plutôt les moules ou les crevettes ? », « Dix ou vingt kilos ? ») "
    "ou de dire juste le produit et la quantité.",
    "Toujours pas compris : la ligne est sans doute mauvaise. Dis-le simplement, sans t'excuser trois fois, et "
    "propose de le rappeler dans quelques minutes (s'il accepte : record_call_outcome callback dans 10 minutes).",
]


def clarify_note(route: Route, call: "OutboundCall", streak: int, avoid: list[str]) -> str:
    """The switchboard note of a clarification turn: what was understood, how to ask, what not to say again."""
    understood = []
    if route.offer and route.offer_confidence >= 0.6:
        understood.append(next((o["name"] for o in call.offers if o["offerId"] == route.offer), route.offer))
    numbers = re.findall(r"\b\d+(?:[,.]\d+)?\b", route.text)
    if numbers:
        understood.append(f"le nombre {numbers[0]}")
    step = CLARIFY_STEPS[min(streak, len(CLARIFY_STEPS)) - 1]
    said = f" Tu as compris : {', '.join(understood)}." if understood else ""
    no_repeat = f" Ne reprends aucune de ces formulations déjà dites : {' / '.join(f'« {a} »' for a in avoid[-3:])}." if avoid else ""
    return f"{NOTE_SEP} : phrase du client mal comprise (transcription : « {route.text} »).{said} {step}{no_repeat}]"


DONE_LINES = {  # said by code when the model returns nothing after an action the code did (rare, but never silence)
    "confirmed": ["C'est validé, livraison {day}. Merci et bonne journée !", "Parfait, c'est enregistré pour {day}. Belle journée !",
                  "C'est noté, vous serez livré {day}. Merci beaucoup !"],
    "declined": ["Très bien, merci pour votre temps. Bonne journée !", "Pas de problème, merci et bonne journée !"],
    "do_not_call": ["Pardon pour le dérangement, vous ne serez plus appelé. Bonne journée !"],
    "goodbye": ["Au revoir, bonne journée !", "Au revoir, et bon service !"],
    "clarify": ["Pardon, je vous ai mal entendu : vous pouvez répéter ?", "Excusez-moi, la ligne a coupé : vous disiez ?"],
}


@dataclass
class Plan:
    fast: bool  # the small model answers (a simple turn), else the big one
    note: str | None = None  # switchboard note appended to the client's words (what was done, or a hint)
    action: str | None = None  # what the code did in the ERP, for the logs / timings ("confirmed", "declined"…)
    filler_first: bool = False  # Jev knows the model will work first (quantities, a price): waiting phrase right away

    @property
    def llms(self) -> list[LLMClient]:
        """The model to use, then the fallback."""
        main, fast = get_llm_client(), get_fast_llm_client()
        return [fast, main] if self.fast and fast is not main else [main, fast] if fast is not main else [main]


RECAP_QUESTION = re.compile(r"\b(valide|validez|confirme|confirmez|c'est bon pour vous|je lance la commande)\b", re.I)


def unclear(route: Route | None, stt_confidence: float | None) -> bool:
    """Not understood: Jev finds the sentence unclear, or the transcript is doubtful and Jev is not sure either."""
    if route is None:
        return False
    if route.clear < settings.voice.clarity_min:
        return True
    return stt_confidence is not None and stt_confidence < settings.voice.stt_confidence_min and route.clear < 0.7


async def plan_turn(call: OutboundCall, route: Route | None, actor_name: str | None = None, agent_said: str = "",
                    stt_confidence: float | None = None, clarify_streak: int = 0, clarify_avoid: list[str] | None = None) -> Plan:  # fmt: skip
    """What to do with this turn, from Jev's reading of the client's words. Never raises: on any doubt or ERP error,
    the big model takes the turn with nothing done in advance. `agent_said`: the agent's previous reply — a "oui"
    confirms the order by code only if that reply asked to validate a recap (not "je vous ajoute des bulots ?")."""
    if unclear(route, stt_confidence):  # ask, don't guess: the small model phrases a short, varied question
        return Plan(True, clarify_note(route, call, clarify_streak + 1, clarify_avoid or []), "clarify")
    if route is None or route.confidence < 0.6:
        return Plan(fast=False)
    erp = ErpClient("voice_agent", actor_name)
    try:
        state = await erp.get(f"/api/campaign-calls/{enc(call.call_id)}")
    except ErpError:
        return Plan(fast=False)
    is_open = state.get("status") in ("pending", "in_progress")
    has_lines = bool(state.get("accepted")) and state.get("orderStatus") not in (None, "cancelled")
    draft = has_lines and state.get("orderStatus") == "draft"
    sure = route.confidence >= SURE
    try:
        if route.intent == "valide" and sure and draft and RECAP_QUESTION.search(agent_said):
            res = await erp.post(f"/api/campaign-calls/{enc(call.call_id)}/confirm")
            total = str(round(res["order"]["totalHt"], 2)).replace(".", ",")
            return Plan(True, f"{NOTE_SEP} : il a dit oui. Commande confirmée et appel enregistré (total {total} € HT, "
                              f"livraison {call.brief.get('deliveryOn')}). Remercie-le et dis au revoir en une phrase, "
                              "sans redire la commande.]", "confirmed", filler_first=True)  # fmt: skip
        if route.intent == "ne_plus_appeler" and sure and is_open:
            await erp.patch(f"/api/campaign-calls/{enc(call.call_id)}", {"status": "do_not_call", "summary": "Ne veut plus être appelé"})
            return Plan(True, f"{NOTE_SEP} : c'est enregistré, il ne sera plus appelé. Excuse-toi en une phrase et dis au revoir.]", "do_not_call")
        if route.intent in ("refuse", "au_revoir") and sure and is_open and not has_lines:
            await erp.patch(f"/api/campaign-calls/{enc(call.call_id)}", {"status": "declined", "summary": f"Refus : « {route.text[:160]} »"})
            return Plan(True, f"{NOTE_SEP} : refus enregistré. Remercie et dis au revoir en une phrase, sans relancer.]", "declined")
    except ErpError as e:
        print(f"[ai] router action failed ({e}): the big model takes the turn", flush=True)
        return Plan(fast=False)
    if route.intent == "au_revoir" and not is_open:  # call already recorded and goodbye already said
        return Plan(True, f"{NOTE_SEP} : appel terminé. Réponds juste au revoir, en trois mots au plus.]", "goodbye")
    if route.intent in ("robot", "rappel", "question") and route.confidence >= SURE:
        return Plan(True)
    if route.intent in ("choisit", "negocie") and route.offer and route.offer_confidence >= HINT:
        name = next((o["name"] for o in call.offers if o["offerId"] == route.offer), route.offer)
        return Plan(False, f"{NOTE_SEP} : il parle sans doute de : {name} ({route.offer}).]", filler_first=route.confidence >= SURE)
    if route.intent in ("choisit", "negocie") and route.confidence >= SURE:
        return Plan(False, filler_first=True)
    return Plan(fast=False)


async def run_outbound(
    working: list[ChatMessage], call: OutboundCall, plan: Plan, actor_name: str | None = None
) -> AsyncIterator[ChatStreamEvent]:
    """run_agent for an outbound turn, appending to `working`. If the model fails or stays silent before its first
    word / tool call, the turn is replayed on the other model; if that fails too, a short excuse is said."""
    start = len(working)
    if plan.note and working and working[-1].role == "user":
        working[-1] = ChatMessage(role="user", content=f"{working[-1].content}{plan.note}")
    for llm in plan.llms:
        del working[start:]
        gen = run_agent(
            working, llm=llm, tools=outbound_tools(), mode="outbound", actor_name=actor_name, system_extra=call.prompt,
            speak_only=plan.action is not None,  # the code already acted: just say it (no tool call to wait for)
        )  # fmt: skip
        started, retry, said = False, False, False
        try:
            while True:
                try:
                    nxt = gen.__anext__()
                    ev = await (nxt if started else asyncio.wait_for(nxt, FIRST_EVENT_TIMEOUT_S))
                except StopAsyncIteration:
                    break
                except TimeoutError:
                    retry = True
                    break
                if isinstance(ev, ErrorEvent) and not started:
                    retry = True
                    break
                started = started or isinstance(ev, (TextEvent, ToolCallEvent))
                said = said or (isinstance(ev, TextEvent) and bool(ev.delta.strip()))
                yield ev
        finally:
            await gen.aclose()
        if not retry:
            if plan.action and not said:  # the code acted but the model said nothing: never leave the client in silence
                text = random.choice(DONE_LINES.get(plan.action, DONE_LINES["goodbye"])).format(day=call.brief.get("deliveryOn") or "demain")
                last = working[-1] if working else None
                if last and last.role == "assistant" and not last.tool_calls:
                    working[-1] = ChatMessage(role="assistant", content=text)
                else:
                    working.append(ChatMessage(role="assistant", content=text))
                yield TextEvent(delta=text)
            return
        print(f"[ai] {llm.model} failed or stayed silent: next model", flush=True)
    del working[start:]
    working.append(ChatMessage(role="assistant", content=SORRY))
    yield TextEvent(delta=SORRY)
