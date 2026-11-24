"""Clearance campaigns: short-dated lots offered at a discount to chosen clients, one outbound call per client.

Input = the clearance model's JSON (POST /api/campaigns/import): offers (a lot, a quantity, an opening and a maximum
discount) + targets (a client, the offers to propose, a score). The business rules live here, not in the agent:
  - who is NOT called (blocked client, "do not call" note, nothing deliverable before expiry) → status "skipped";
  - what is left of an offer = offer qty − what the campaign's calls sold (orders not cancelled), capped by the lot;
  - the discounted price = the client's usual price (negotiated prices included) × (1 − discount), discount ≤ max;
  - an accepted offer becomes a line of ONE draft order per call, at that price (confirmation = the usual
    POST /api/orders/{id}/confirm, after the client agreed to the recap).

Why not a PriceRule: a negotiated ClientPrice always wins over rules, so the discount would be lost for exactly
the clients who buy the product the most.
"""

from __future__ import annotations

from datetime import timedelta

from app import crud
from app.core.activity import Ctx, local_now, local_now_str, log_event, now, today
from app.core.db import Doc, transactional
from app.core.errors import bad_request, conflict
from app.core.utils import cmp, compact, fmt_eur, fr_day, fr_num, js_str, normalize, round2, round3, sort_by
from app.services import client_events, clients, context, orders, pricing

OPEN = ("pending", "in_progress")
OUTCOMES = ("accepted", "declined", "callback", "no_answer", "do_not_call")
STATUS_FR = {
    "pending": "à appeler",
    "skipped": "écarté",
    "in_progress": "en cours",
    "accepted": "accepté",
    "declined": "refusé",
    "callback": "à rappeler",
    "no_answer": "sans réponse",
    "do_not_call": "ne plus appeler",
}
DO_NOT_CALL_NOTE = "Ne plus appeler pour les offres de déstockage"
_DO_NOT_CALL_WORDS = ("ne plus appeler", "ne pas appeler", "ne plus le rappeler", "pas de demarchage")


# ───────── Reads ─────────


def _offers(campaign: Doc) -> dict[str, Doc]:
    return {o["id"]: o for o in campaign["offers"]}


def _calls(campaign_id: str) -> list[Doc]:
    calls = crud.campaign_calls.filter(lambda c: c["campaignId"] == campaign_id)
    return sort_by(calls, lambda a, b: cmp(a["rank"], b["rank"]))


def _live_lines(call: Doc) -> list[Doc]:
    """Accepted lines that still count: their order exists and is not cancelled."""
    if not call.get("accepted") or not call.get("orderId"):
        return []
    order = crud.orders.get(call["orderId"])
    return call["accepted"] if order and order["status"] != "cancelled" else []


def _sold(campaign_id: str, skip: tuple[str, str] | None = None) -> dict[str, float]:
    """offerId → qty accepted in the campaign's calls. `skip` = (callId, offerId) left out (a "yes" being revised)."""
    sold: dict[str, float] = {}
    for call in _calls(campaign_id):
        for line in _live_lines(call):
            if skip != (call["id"], line["offerId"]):
                sold[line["offerId"]] = sold.get(line["offerId"], 0) + line["qty"]
    return sold


def _remaining(offer: Doc, sold: dict[str, float]) -> float:
    left = offer["qty"] - sold.get(offer["id"], 0)
    if offer.get("lotId"):
        lot = crud.lots.get(offer["lotId"])
        left = min(left, lot["qtyRemaining"] if lot else 0)
    return round3(max(0.0, left))


def _deliverable(offer: Doc, delivery_date: str) -> bool:
    """Delivered at the latest on the expiry day."""
    return delivery_date <= offer["expiresAt"]


def _wants_no_calls(client: Doc) -> bool:
    return any(w in normalize(n["text"]) for n in client.get("notes") or [] for w in _DO_NOT_CALL_WORDS)


def _open_offers(call: Doc, campaign: Doc, sold: dict[str, float]) -> list[Doc]:
    """The call's offers that can still be sold to this client: stock left, delivered before expiry."""
    offers = _offers(campaign)
    delivery = clients.next_delivery_date(crud.clients.require(call["clientId"]))
    return [o for oid in call["offerIds"] if (o := offers.get(oid)) and _remaining(o, sold) > 0 and _deliverable(o, delivery)]


def _offer_view(offer: Doc, sold: dict[str, float]) -> Doc:
    p = crud.products.get(offer["productId"]) or {}
    return {
        **offer,
        "productName": p.get("name", offer["productId"]),
        "unit": p.get("unit", ""),
        "listPrice": p.get("price", 0),
        "sold": round3(sold.get(offer["id"], 0)),
        "remaining": _remaining(offer, sold),
    }


def call_view(call: Doc, campaign: Doc | None = None, sold: dict[str, float] | None = None) -> Doc:
    campaign = campaign or crud.campaigns.require(call["campaignId"])
    sold = _sold(campaign["id"]) if sold is None else sold
    client = crud.clients.get(call["clientId"])
    order = crud.orders.get(call["orderId"]) if call.get("orderId") else None
    return {
        **call,
        "clientName": client["name"] if client else call["clientId"],
        **({"orderNumber": order["number"], "orderStatus": order["status"]} if order else {}),
        "openOffers": len(_open_offers(call, campaign, sold)),
    }


def _stats(calls: list[Doc]) -> Doc:
    qty = revenue = discount = 0.0
    for call in calls:
        for line in _live_lines(call):
            amount = line["qty"] * line["unitPrice"]
            qty += line["qty"]
            revenue += amount
            discount += amount * line["discountPct"] / (100 - line["discountPct"])  # usual price − discounted price
    return {
        "calls": len(calls),
        "done": sum(c["status"] not in OPEN for c in calls),
        "accepted": sum(c["status"] == "accepted" for c in calls),
        "declined": sum(c["status"] in ("declined", "do_not_call") for c in calls),
        "qtySold": round3(qty),
        "revenueHt": round2(revenue),
        "discountGivenHt": round2(discount),
    }


def list_campaigns() -> list[Doc]:
    """Newest first."""
    rows = sort_by(crud.campaigns.all(), lambda a, b: cmp(b["createdAt"], a["createdAt"]))
    return [{**c, "stats": _stats(_calls(c["id"]))} for c in rows]


def campaign_detail(id: str) -> Doc:
    campaign = crud.campaigns.require(id)
    calls = _calls(id)
    sold = _sold(id)
    return {
        **campaign,
        "stats": _stats(calls),
        "offers": [_offer_view(o, sold) for o in campaign["offers"]],
        "calls": [call_view(c, campaign, sold) for c in calls],
    }


def next_call(campaign_id: str) -> Doc:
    """The best-ranked call to make now: pending, or a callback whose time has come, with an offer still open."""
    campaign = crud.campaigns.require(campaign_id)
    if campaign["status"] != "active":
        return {"pending": 0}
    sold = _sold(campaign_id)
    t = local_now_str()
    due = [c for c in _calls(campaign_id) if c["status"] == "pending" or (c["status"] == "callback" and (c.get("callbackAt") or "")[:16] <= t)]
    ready = [c for c in due if _open_offers(c, campaign, sold)]
    return {**({"call": call_view(ready[0], campaign, sold)} if ready else {}), "pending": len(ready)}


def call_brief(call_id: str) -> Doc:
    """Everything the agent needs for one call, compact (what it reads at every step)."""
    call = crud.campaign_calls.require(call_id)
    campaign = crud.campaigns.require(call["campaignId"])
    client = clients.client_summary(call["clientId"])
    delivery = clients.next_delivery_date(client)
    usual = {line["productId"]: line["avgQty"] for line in clients.usual_basket(client["id"])}
    offers = []
    for o in _open_offers(call, campaign, _sold(campaign["id"])):
        p = crud.products.require(o["productId"])
        price = pricing.price_for(client["id"], p["id"])
        offers.append(compact({
            "offerId": o["id"],
            "productId": p["id"],
            "productName": p["name"],
            "unit": p["unit"],
            "origin": p.get("origin"),
            "caliber": p.get("caliber"),
            "remaining": _remaining(o, _sold(campaign["id"], skip=(call["id"], o["id"]))),
            "expiresAt": o["expiresAt"],
            "usualQty": usual.get(p["id"]),
            "usualPrice": price,
            "offerPrice": round2(price * (1 - o["discountPct"] / 100)),
            "discountPct": o["discountPct"],
            "maxDiscountPct": o["maxDiscountPct"],
            "floorPrice": round2(price * (1 - o["maxDiscountPct"] / 100)),
        }))  # fmt: skip
    contact = (client.get("contacts") or [None])[0]
    entries = context.context_for("client", client["id"])["entries"]
    return compact({
        "callId": call["id"],
        "campaignId": campaign["id"],
        "status": call["status"],
        "attempts": call["attempts"],
        "reason": call.get("reason"),
        "deliveryDate": delivery,
        "client": compact({
            "id": client["id"],
            "name": client["name"],
            "type": client["type"],
            "city": client["city"],
            "contact": compact({"name": contact["name"], "role": contact.get("role")}) if contact else None,
            "status": client["status"],
            "preferences": client.get("preferences") or "",
            "notes": [n["text"] for n in reversed(client.get("notes") or [])][:3],
            "deliveryDays": client.get("deliveryDays") or [],
            "overdue": client["overdue"],
            "openIssues": client.get("openIssues", 0),
        }),
        "offers": offers,
        "context": [f"{e['title']} : {e['body'][:300]}" for e in entries[:6]],
        "accepted": _live_lines(call) or None,
        "orderId": call.get("orderId"),
        "previousCalls": (call.get("conversations") or [])[-2:] or None,
        "nowLocal": local_now_str(),
        "nowSpoken": _spoken_now(),
    })  # fmt: skip


DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


def _spoken_now() -> str:
    """« mardi 24 novembre, 14 h 50 » (what the agent reads to place a callback)."""
    t = local_now()
    return f"{DAYS[t.weekday()]} {t.day} {MONTHS[t.month - 1]}, {t.hour} h {t.minute:02d}"


@transactional
def save_transcript(ctx: Ctx, call_id: str, lines: list[str]) -> Doc:
    """Keep what was said in this attempt (last 60 lines), so a callback picks the conversation up where it stopped."""
    call = crud.campaign_calls.require(call_id)
    lines = [line.strip() for line in lines if line.strip()][-60:]
    if not lines:
        return call_view(call)
    entry = compact({"at": local_now_str(), "status": call["status"], "summary": call.get("summary"), "lines": lines})
    updated = crud.campaign_calls.put({**call, "conversations": [*(call.get("conversations") or []), entry][-3:]})
    return call_view(updated)


def callbacks() -> Doc:
    """Callbacks of the active campaigns: due (time passed, an offer still open) and upcoming, soonest first."""
    t = local_now_str()
    due, upcoming = [], []
    for campaign in crud.campaigns.all():
        if campaign["status"] != "active":
            continue
        sold = _sold(campaign["id"])
        for c in _calls(campaign["id"]):
            if c["status"] != "callback" or not c.get("callbackAt") or not _open_offers(c, campaign, sold):
                continue
            (due if c["callbackAt"][:16] <= t else upcoming).append(call_view(c, campaign, sold))
    key = lambda c: c.get("callbackAt") or ""  # noqa: E731
    return {"now": t, "due": sorted(due, key=key), "upcoming": sorted(upcoming, key=key)}


def _check_active(campaign: Doc) -> None:
    if campaign["status"] != "active":
        raise conflict("campaign_closed", f"La campagne {campaign['name']} est clôturée")


def _quote(call: Doc, campaign: Doc, offer_id: str, pct: float | None, qty: float | None) -> Doc:
    if offer_id not in call["offerIds"]:
        raise bad_request("offer_not_in_call", f"L'offre {offer_id} n'est pas proposée à ce client")
    offer = _offers(campaign)[offer_id]
    p = crud.products.require(offer["productId"])
    pct = offer["discountPct"] if pct is None else pct
    if pct > offer["maxDiscountPct"]:
        raise bad_request(
            "discount_too_high",
            f"Remise trop forte sur {p['name']} : {js_str(pct)} % demandés, {js_str(offer['maxDiscountPct'])} % au maximum",
        )
    client = crud.clients.require(call["clientId"])
    delivery = clients.next_delivery_date(client)
    if not _deliverable(offer, delivery):
        raise bad_request(
            "expires_before_delivery",
            f"{p['name']} périme {fr_day(offer['expiresAt'])}, avant la prochaine livraison de {client['name']} ({fr_day(delivery)})",
        )
    remaining = _remaining(offer, _sold(campaign["id"], skip=(call["id"], offer_id)))
    if qty is not None and qty > remaining:
        raise bad_request("not_enough_left", f"Il ne reste que {fr_num(remaining)} {p['unit']} de {p['name']} en déstockage")
    usual = pricing.price_for(client["id"], p["id"], qty or 1)
    unit_price = round2(usual * (1 - pct / 100))
    return compact({
        "offerId": offer_id,
        "productName": p["name"],
        "unit": p["unit"],
        "discountPct": pct,
        "usualPrice": usual,
        "unitPrice": unit_price,
        "qty": qty,
        "totalHt": round2(unit_price * qty) if qty is not None else None,
        "remaining": remaining,
        "deliveryDate": delivery,
        "expiresAt": offer["expiresAt"],
    })  # fmt: skip


def quote(call_id: str, offer_id: str, pct: float | None = None, qty: float | None = None) -> Doc:
    """GET /api/campaign-calls/{id}/quote: the price at a given discount (refused beyond the maximum)."""
    call = crud.campaign_calls.require(call_id)
    campaign = crud.campaigns.require(call["campaignId"])
    if pct is not None and not 0 <= pct < 100:
        raise bad_request("invalid_pct", "La remise doit être entre 0 et 100 %")
    if qty is not None and not qty > 0:
        raise bad_request("invalid_qty", "La quantité doit être positive")
    return _quote(call, campaign, offer_id, pct, qty)


# ───────── Writes ─────────


def _skip_reason(client: Doc, offers: list[Doc], delivery: str) -> str | None:
    if client["status"] == "blocked":
        return "client bloqué"
    if _wants_no_calls(client) or crud.campaign_calls.filter(lambda c: c["clientId"] == client["id"] and c["status"] == "do_not_call"):
        return "ne veut plus être appelé"
    if not any(_deliverable(o, delivery) for o in offers):
        return f"prochaine livraison {fr_day(delivery)}, après la date de péremption"
    return None


@transactional
def import_campaign(ctx: Ctx, input: Doc) -> Doc:
    """The model's JSON → a campaign + one call per client (best score first, then earliest expiry)."""
    cid = input.get("campaignId") or crud.campaigns.next_id("cmp_")
    if crud.campaigns.get(cid):
        raise conflict("campaign_exists", f"La campagne {cid} existe déjà")

    offers: list[Doc] = []
    for o in input["offers"]:
        if any(x["id"] == o["offerId"] for x in offers):
            raise bad_request("duplicate_offer", f"Offre en double : {o['offerId']}")
        p = crud.products.require(o["productId"])
        lot = crud.lots.require(o["lotId"]) if o.get("lotId") else None
        if lot and lot["productId"] != p["id"]:
            raise bad_request("lot_mismatch", f"Le lot {lot['id']} n'est pas un lot de {p['name']}")
        expires = o.get("expiresAt") or (lot["expiresAt"] if lot else None)
        if not expires:
            raise bad_request("expiry_required", f"Offre {o['offerId']} : donner lotId ou expiresAt")
        if o["discountPct"] > o["maxDiscountPct"]:
            raise bad_request("invalid_discount", f"Offre {o['offerId']} : la remise d'ouverture dépasse la remise maximale")
        offers.append(compact({
            "id": o["offerId"],
            "productId": p["id"],
            "lotId": lot["id"] if lot else None,
            "qty": o["qty"],
            "expiresAt": expires,
            "discountPct": o["discountPct"],
            "maxDiscountPct": o["maxDiscountPct"],
            "experiment": o.get("experiment"),
        }))  # fmt: skip
    by_id = {o["id"]: o for o in offers}

    grouped: dict[str, Doc] = {}  # one call per client, even if the model lists it several times
    for target in input["targets"]:
        client = crud.clients.require(target["clientId"])
        for oid in target["offerIds"]:
            if oid not in by_id:
                raise bad_request("unknown_offer", f"Offre inconnue pour {client['name']} : {oid}")
        g = grouped.setdefault(client["id"], {"offerIds": [], "score": None, "reasons": []})
        g["offerIds"] += [oid for oid in target["offerIds"] if oid not in g["offerIds"]]
        if target.get("score") is not None:
            g["score"] = target["score"] if g["score"] is None else max(g["score"], target["score"])
        if target.get("reason"):
            g["reasons"].append(target["reason"])

    ranked = sorted(
        grouped.items(),
        key=lambda kv: (-(kv[1]["score"] or 0), min(by_id[oid]["expiresAt"] for oid in kv[1]["offerIds"]), kv[0]),
    )
    campaign = {
        "id": cid,
        "name": input.get("name") or f"Déstockage du {fr_day(today()).removeprefix('le ')}",
        "status": "active",
        "offers": offers,
        "createdAt": now(),
        "createdBy": ctx.actor_name or ctx.actor,
    }
    crud.campaigns.put(campaign)
    if input.get("closeOthers"):
        for other in crud.campaigns.filter(lambda c: c["status"] == "active" and c["id"] != cid):
            crud.campaigns.put({**other, "status": "closed"})
            log_event(ctx, "campaign.updated", "campaign", other["id"], f"Campagne « {other['name']} » clôturée (remplacée par {cid})")
    skipped = 0
    for rank, (client_id, g) in enumerate(ranked, 1):
        client = crud.clients.require(client_id)
        delivery = clients.next_delivery_date(client)
        skip = _skip_reason(client, [by_id[oid] for oid in g["offerIds"]], delivery)
        skipped += bool(skip)
        crud.campaign_calls.put(compact({
            "id": crud.campaign_calls.next_id("ccl_"),
            "campaignId": cid,
            "clientId": client_id,
            "offerIds": g["offerIds"],
            "score": g["score"] or 0,
            "reason": "; ".join(g["reasons"]) or None,
            "rank": rank,
            "status": "skipped" if skip else "pending",
            "skipReason": skip,
            "deliveryDate": delivery,
            "attempts": 0,
        }))  # fmt: skip
    log_event(ctx, "campaign.imported", "campaign", cid,
              f"Campagne « {campaign['name']} » importée : {len(offers)} offre(s), {len(ranked)} client(s) dont {skipped} écarté(s)")  # fmt: skip
    return campaign_detail(cid)


@transactional
def update_campaign(ctx: Ctx, id: str, patch: Doc) -> Doc:
    campaign = crud.campaigns.require(id)
    updated = crud.campaigns.put({**campaign, **compact(patch)})
    if updated["status"] != campaign["status"]:
        verb = "clôturée" if updated["status"] == "closed" else "rouverte"
        log_event(ctx, "campaign.updated", "campaign", id, f"Campagne « {updated['name']} » {verb}")
    return campaign_detail(id)


@transactional
def accept_offer(ctx: Ctx, call_id: str, input: Doc) -> Doc:
    """The client said yes: add (or update) the line of the call's draft order, at the discounted price."""
    call = crud.campaign_calls.require(call_id)
    campaign = crud.campaigns.require(call["campaignId"])
    _check_active(campaign)
    if call["status"] in ("skipped", "do_not_call"):
        raise conflict("call_closed", f"Cet appel est {STATUS_FR[call['status']]} : rien ne peut y être vendu")
    offer_id, qty = input["offerId"], input["qty"]
    q = _quote(call, campaign, offer_id, input.get("discountPct"), qty)
    offer = _offers(campaign)[offer_id]
    lot = f", lot {offer['lotId']}" if offer.get("lotId") else ""
    note = f"Déstockage −{js_str(q['discountPct'])} % (campagne {campaign['id']}{lot})"
    line = {"productId": offer["productId"], "qty": qty, "unitPrice": q["unitPrice"], "note": note}

    order = crud.orders.get(call["orderId"]) if call.get("orderId") else None
    if order and order["status"] not in ("draft", "confirmed"):
        order = None  # cancelled (or already gone further): start a new order
    accepted = _live_lines(call) if order else []
    previous = next((a for a in accepted if a["offerId"] == offer_id), None)
    if order and previous and any(l["id"] == previous.get("lineId") for l in order["lines"]):
        detail = orders.update_line(ctx, order["id"], previous["lineId"], {"qty": qty, "unitPrice": q["unitPrice"], "note": note})
        line_id = previous["lineId"]
    elif order:
        detail = orders.add_line(ctx, order["id"], line)
        line_id = detail["lines"][-1]["id"]
    else:
        detail = orders.create_order(ctx, {
            "clientId": call["clientId"],
            "deliveryDate": q["deliveryDate"],
            "lines": [line],
            "note": f"Campagne de déstockage : {campaign['name']}",
        })  # fmt: skip
        line_id = detail["lines"][0]["id"]

    entry = {"offerId": offer_id, "productId": offer["productId"], "qty": qty, "discountPct": q["discountPct"],
             "unitPrice": q["unitPrice"], "lineId": line_id}  # fmt: skip
    updated = crud.campaign_calls.put({
        **call,
        "status": "in_progress" if call["status"] == "pending" else call["status"],
        "orderId": detail["id"],
        "accepted": [a for a in accepted if a["offerId"] != offer_id] + [entry],
    })  # fmt: skip
    client = crud.clients.require(call["clientId"])
    log_event(ctx, "campaign.offer_accepted", "campaign", campaign["id"],
              f"{client['name']} prend {fr_num(qty)} {q['unit']} de {q['productName']} à {fmt_eur(q['unitPrice'])} "
              f"(−{js_str(q['discountPct'])} %), commande {detail['number']}")  # fmt: skip
    return {"call": call_view(updated, campaign), "order": orders.order_detail(detail["id"])}


@transactional
def update_call(ctx: Ctx, call_id: str, patch: Doc) -> Doc:
    """Status changes: in_progress (call starts), an outcome (call ends: logged in the client's history), pending (requeue)."""
    call = crud.campaign_calls.require(call_id)
    campaign = crud.campaigns.require(call["campaignId"])
    client = crud.clients.require(call["clientId"])
    status = patch.get("status") or call["status"]
    changes: Doc = {}
    minutes = patch.pop("callbackInMinutes", None) or patch.pop("callback_in_minutes", None)
    if minutes:  # « dans 10 minutes »: from the company's clock
        patch["callbackAt"] = (local_now() + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M")
    elif patch.get("callbackAt"):
        patch["callbackAt"] = patch["callbackAt"][:16]
    if status != call["status"]:
        if status == "in_progress":
            _check_active(campaign)
            changes = {"startedAt": now(), "attempts": call["attempts"] + 1}
        elif status in OUTCOMES or status == "skipped":
            changes = {"endedAt": now()}
        if status == "callback" and not (patch.get("callbackAt") or call.get("callbackAt")):
            raise bad_request("callback_at_required", "Indiquer quand rappeler (callbackAt)")
        if status == "accepted" and not _live_lines(call):
            raise conflict("nothing_accepted", "Aucune offre acceptée sur cet appel : passer d'abord par /accept")
    updated = crud.campaign_calls.put({**call, **compact(patch), **changes})

    if status in OUTCOMES and call["status"] in OPEN:  # the call just ended: one line in the client's history
        products = list(dict.fromkeys(_offers(campaign)[oid]["productId"] for oid in call["offerIds"]))
        client_events.create_client_event(ctx, client["id"], compact({
            "type": "call",
            "title": f"Appel déstockage : {STATUS_FR[status]}",
            "description": patch.get("summary") or call.get("summary") or "",
            "productIds": products,
            "orderId": call.get("orderId"),
        }))  # fmt: skip
    if status == "do_not_call" and not _wants_no_calls(client):
        clients.add_client_note(ctx, client["id"], DO_NOT_CALL_NOTE, ctx.actor_name or "Agent vocal")
    summary = f" — {patch['summary']}" if patch.get("summary") else ""
    log_event(ctx, "campaign.call_updated", "campaign", campaign["id"], f"Appel {client['name']} : {STATUS_FR[status]}{summary}")
    return call_view(updated, campaign)


@transactional
def confirm_call(ctx: Ctx, call_id: str, summary: str | None = None) -> Doc:
    """The client agreed to the recap: confirm the call's draft order AND close the call as accepted, in one step
    (one model round trip less on the slowest turn of the call). Calling it again on a confirmed order is harmless."""
    call = crud.campaign_calls.require(call_id)
    order = crud.orders.get(call["orderId"]) if call.get("orderId") else None
    if not order or order["status"] == "cancelled" or not _live_lines(call):
        raise conflict("nothing_accepted", "Aucune offre acceptée sur cet appel : passer d'abord par accept_offer")
    if order["status"] == "draft":
        orders.confirm_order(ctx, order["id"])
    if call["status"] in OPEN:
        auto = ", ".join(
            f"{fr_num(a['qty'])} × {crud.products.require(a['productId'])['name']} à −{js_str(a['discountPct'])} %" for a in _live_lines(call)
        )
        update_call(ctx, call_id, {"status": "accepted", "summary": summary or f"Commande {order['number']} confirmée : {auto}"})
    updated = crud.campaign_calls.require(call_id)
    return {"call": call_view(updated), "order": orders.order_detail(order["id"])}
