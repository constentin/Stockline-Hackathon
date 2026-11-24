"""Outbound clearance calls: the agent calls a client to sell short-dated lots at a discount.

The campaign (offers, clients to call, what is left) lives in the ERP (/api/campaigns, /api/campaign-calls).
Prices, the maximum discount and the remaining quantities are enforced there: these tools only relay.
"""

from typing import Literal

from pydantic import Field

from .erp import enc, pick
from .history import without_none
from .orders import summarize_order
from .registry import ToolContext, tool



def call_id_field():  # a fresh Field per parameter
    return Field(description="L'appel en cours, ex. ccl_000001 (dans la fiche d'appel)")


def offer_id_field():
    return Field(description="ex. off_1 (dans la fiche d'appel)")


# Expected on every clearance line (the lot is short-dated on purpose): not worth the model's attention.
EXPECTED_WARNINGS = {"expiring_soon"}


@tool
async def get_call_brief(ctx: ToolContext, call_id: str = call_id_field()) -> dict:
    """
    Fiche de l'appel sortant : le client (interlocuteur, préférences, notes), les offres de déstockage encore
    disponibles (quantité restante, prix habituel, prix proposé, prix plancher à ne jamais annoncer), ce qu'il a déjà
    accepté. À rappeler si un autre client a pu acheter entre-temps ou après une erreur de quantité.
    Outbound call brief.
    """
    return await ctx.erp.get(f"/api/campaign-calls/{enc(call_id)}/brief")


@tool
async def quote_offer(
    ctx: ToolContext,
    call_id: str = call_id_field(),
    offer_id: str = offer_id_field(),
    discount_pct: float | None = Field(None, description="Remise en %, par défaut celle de l'offre ; refusée au-delà du maximum"),
    qty: float | None = Field(None, description="Quantité dans l'unité du produit, pour avoir le total"),
) -> dict:
    """
    Prix d'une offre de déstockage pour ce client à une remise donnée (prix au kilo/à la pièce, total, date de livraison).
    À utiliser quand le client demande un prix ou négocie : monte la remise par paliers, jamais au-delà du maximum.
    Quote a clearance offer.
    """
    q = await ctx.erp.get(
        f"/api/campaign-calls/{enc(call_id)}/quote", without_none({"offerId": offer_id, "pct": discount_pct, "qty": qty})
    )
    return pick(q, "productName", "unit", "discountPct", "usualPrice", "unitPrice", "qty", "totalHt", "remaining", "deliveryDate", "expiresAt")


@tool
async def accept_offer(
    ctx: ToolContext,
    call_id: str = call_id_field(),
    offer_id: str = offer_id_field(),
    qty: float = Field(description="Quantité acceptée dans l'unité du produit"),
    discount_pct: float | None = Field(None, description="La remise convenue en %, par défaut celle de l'offre"),
) -> dict:
    """
    Le client accepte une offre : ajoute la ligne au prix remisé sur la commande BROUILLON de l'appel (une seule
    commande par appel ; la même offre à nouveau = la quantité est mise à jour). Ne confirme rien : récapitule, puis
    confirm_order après un oui clair. Accept a clearance offer (draft order line).
    """
    body = without_none({"offerId": offer_id, "qty": qty, "discountPct": discount_pct})
    res = await ctx.erp.post(f"/api/campaign-calls/{enc(call_id)}/accept", body)
    order = summarize_order(res["order"])
    order["warnings"] = [w for w in order["warnings"] if w.get("code") not in EXPECTED_WARNINGS]
    return order


YES_THRESHOLD = 0.35  # below: Jev is fairly sure the client did NOT agree (hesitation, question, change)


def last_exchange(messages: list) -> tuple[str, str]:
    """(what the agent said before the client's last words, the client's last words)."""
    i = next((i for i in range(len(messages) - 1, -1, -1) if messages[i].role == "user"), None)
    if i is None:
        return "", ""
    agent = next((m.content for m in reversed(messages[:i]) if m.role == "assistant" and m.content), "")
    return (agent or "").split("\n[Standard")[0], messages[i].content or ""


async def client_agreed(messages: list) -> float | None:
    """Jev: probability that the client's last words are a clear yes to the recap. None = no Jev / Jev failed."""
    from app.clients import get_jev_client  # optional (JEV_API_KEY)

    jev = get_jev_client()
    agent, client = last_exchange(messages)
    if not jev or not client:
        return None
    try:
        return await jev.yes_no(
            {"agent": agent, "client": client},
            "Au téléphone, le client accepte-t-il clairement de valider la commande que l'agent vient de récapituler ?",
            yes="oui sans réserve : oui, c'est bon, allez-y, validez, parfait, ça marche",
            no="il hésite, pose une question, change une quantité ou un produit, refuse, ou parle d'autre chose",
        )
    except Exception:
        return None


@tool
async def confirm_call_order(ctx: ToolContext, call_id: str = call_id_field()) -> dict:
    """
    Le client a dit oui au récapitulatif : confirme la commande de l'appel ET enregistre l'appel comme accepté (pas
    besoin de record_call_outcome ensuite). Refusé si le client n'a pas clairement dit oui : redemande alors.
    Confirm the call's order and close the call as accepted.
    """
    p = await client_agreed(ctx.messages)
    if p is not None and p < YES_THRESHOLD:
        raise ValueError("Le client n'a pas clairement dit oui : ne confirme pas, réponds-lui ou redemande s'il valide")
    res = await ctx.erp.post(f"/api/campaign-calls/{enc(call_id)}/confirm")
    order = summarize_order(res["order"])
    order["warnings"] = [w for w in order["warnings"] if w.get("code") not in EXPECTED_WARNINGS]
    return {**order, "callStatus": res["call"]["status"]}


@tool
async def record_call_outcome(
    ctx: ToolContext,
    call_id: str = call_id_field(),
    outcome: Literal["accepted", "declined", "callback", "no_answer", "do_not_call"] = Field(
        description="accepted = au moins une offre prise ; callback = rappeler plus tard ; do_not_call = ne veut plus être appelé"
    ),
    summary: str = Field(description="Une phrase en français sur l'appel, ex. « Prend 20 kg de moules, rappeler jeudi pour les bulots »"),
    callback_at: str | None = Field(
        None, description="Pour callback à une heure dite : YYYY-MM-DDTHH:MM, heure locale (« demain 15 h » ; maintenant = nowLocal de la fiche)"
    ),
    callback_in_minutes: int | None = Field(None, description="Pour callback dans un délai : « dans 10 minutes » → 10, « dans une heure » → 60"),
) -> dict:
    """
    Clôt l'appel sortant sans commande validée (refus, rappel, ne plus appeler, sans réponse) : enregistre le résultat
    dans la campagne et l'historique du client. Inutile après confirm_call_order. Record the call outcome.
    """
    body = without_none({"status": outcome, "summary": summary, "callbackAt": callback_at, "callbackInMinutes": callback_in_minutes})
    res = await ctx.erp.patch(f"/api/campaign-calls/{enc(call_id)}", body)
    return pick(res, "status", "orderNumber", "orderStatus", "callbackAt")
