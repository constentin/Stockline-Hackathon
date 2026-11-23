"""
Outbound clearance calls: what the voice session (and the CLI) need to start and end one call.

    start_call(call_id)  → the call brief (ERP), call marked in_progress, the prompt block built from it
    OPENING_CUE          → the bracketed "user" message that makes the agent speak first
    end_call(...)        → when the caller hangs up and the agent did not record an outcome: classify and record it

The brief goes at the END of the system prompt (run_agent(system_extra=…)): the rest of the prompt stays identical
from one call to the next, so the prompt cache keeps serving it.
"""

import json
from dataclasses import dataclass

from app.agent.prompts import french_date
from app.schemas import ChatMessage
from app.tools import ErpClient
from app.tools.erp import enc

OPENING_CUE = "[Standard : appel sortant, le client vient de décrocher. À toi de parler.]"
HANGUP_CUE = "[Standard : le client a raccroché.]"

OUTCOMES = ("accepted", "declined", "callback", "no_answer", "do_not_call")


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


async def start_call(call_id: str, actor_name: str | None = None) -> OutboundCall:
    """Load the brief and mark the call in progress (one more attempt). Raises ErpError if the ERP refuses."""
    erp = ErpClient("voice_agent", actor_name)
    await erp.patch(f"/api/campaign-calls/{enc(call_id)}", {"status": "in_progress"})
    brief = await erp.get(f"/api/campaign-calls/{enc(call_id)}/brief")
    return OutboundCall(call_id=call_id, brief=with_spoken_dates(brief))


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
        lines.append(f"{'Client' if m.role == 'user' else 'Agent'} : {m.content}")
    return "\n".join(lines)


def recorded_outcome(history: list[ChatMessage]) -> bool:
    """True when the agent already called record_call_outcome during the call."""
    return any(m.role == "assistant" and any(c.name == "record_call_outcome" for c in m.tool_calls or []) for m in history)


async def guess_outcome(history: list[ChatMessage]) -> tuple[str, str]:
    """(outcome, summary) from what was said and done, without the LLM: Jev when available, else simple rules."""
    accepted = any(
        m.role == "assistant" and any(c.name in ("accept_offer", "confirm_order") for c in m.tool_calls or []) for m in history
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
    """Called when the caller hangs up. Records an outcome unless the agent already did. Returns the outcome written."""
    if recorded_outcome(history):
        return None
    outcome, summary = await guess_outcome(history)
    body = {"status": outcome, "summary": summary}
    if outcome == "callback":
        body["callbackAt"] = call.brief.get("deliveryDate", "") + "T10:00"  # no time said: the next delivery day, 10 h
    erp = ErpClient("voice_agent", actor_name)
    await erp.patch(f"/api/campaign-calls/{enc(call.call_id)}", body)
    return outcome
