"""Clearance model: which products will be lost, who can still buy them, and at what discount.

GET /api/campaigns/proposal → a campaign in the IMPORT format (POST /api/campaigns/import) + the analysis behind it.
It is a first, transparent model: the team's own model can replace it as long as it produces the same JSON.

1. Expected loss per product: simulate the next HORIZON_DAYS day by day. Each day sells the usual demand of that weekday
   (average of the last HISTORY_WEEKS weeks) or the orders already confirmed if higher, from the lots in expiry order
   (FEFO). What is left of a lot on its expiry date is lost. Only that quantity is offered: discounting more would sell
   at a discount stock that would have sold at full price.
2. Candidates: active clients who bought the product in the last 60 days, delivered before the offer's sell-by date.
   f = share of their orders with this product, q = their usual quantity, p = their usual price (negotiated included).
3. Response to a discount d (fraction): P(d) = 1 / (1 + exp(-(a + B·d))), a from f (a regular buyer says yes more
   often); quantity taken x(d) = q · (1 + E·d).
4. Expected money recovered: G(d) = Σ P(d) · [(x − c)·p·(1 − d) − c·p·d], c = f·min(x, q) = the part the client would
   have bought anyway at full price (cannibalisation). Clearing is capped at the expected loss. The purchase cost is
   sunk: it does not enter G. Floor: donating gives a tax reduction of DONATION_RATE × cost, so never sell below that.
   discountPct = argmax G, maxDiscountPct = largest d with G ≥ 90 % of the max (the agent's negotiation room).
   An offer whose best G is not positive is dropped: donating recovers more.
5. Each client gets at most MAX_OFFERS_PER_CALL offers (the most valuable for them). Call order (score) = the client's
   expected contribution, normalised to 0–1.
"""

from __future__ import annotations

import math

from app import crud
from app.core.activity import today
from app.core.db import Doc
from app.core.utils import add_days, fr_day, round2, round3, weekday_of
from app.services import clients, pricing

# ── Parameters (tune them, or learn B and the base acceptance from the campaign results: status + discountPct) ──
HORIZON_DAYS = 8
HISTORY_WEEKS = 8
BASKET_DAYS = 60
B_SENSITIVITY = 7.0  # +10 points of discount ≈ doubles the odds of a yes
E_VOLUME = 1.5  # at −20 %, a client takes 30 % more than usual
BASE_ACCEPT = (0.10, 0.40)  # acceptance without discount: occasional → regular buyer
DONATION_RATE = 0.6  # tax reduction of a donation, as a share of cost (to confirm with the accountant)
MIN_LOSS_VALUE = 30.0  # € at cost: below that, no call is worth it
MAX_TARGETS_PER_OFFER = 8
MAX_OFFERS_PER_CALL = 4  # a phone call, not a catalogue: the client's 4 most valuable offers, presented together
COVER = 1.5  # keep calling candidates until they are expected to absorb 1.5 × the loss
GRID = [d / 100 for d in range(5, 65, 5)]


def _logit(p: float) -> float:
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def _weekday_demand(t: str) -> dict[tuple[str, str], float]:
    since = add_days(t, -7 * HISTORY_WEEKS)
    out: dict[tuple[str, str], float] = {}
    for o in crud.orders.rows.values():
        if o["status"] in ("draft", "cancelled") or not since <= o["deliveryDate"] < t:
            continue
        wd = weekday_of(o["deliveryDate"])
        for line in o["lines"]:
            key = (line["productId"], wd)
            out[key] = out.get(key, 0) + line["qty"] / HISTORY_WEEKS
    return out


def _booked(t: str) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    for o in crud.orders.rows.values():
        if o["status"] in ("confirmed", "prepared") and o["deliveryDate"] > t:
            for line in o["lines"]:
                key = (line["productId"], o["deliveryDate"])
                out[key] = out.get(key, 0) + line["qty"]
    return out


def expected_losses(t: str | None = None) -> list[Doc]:
    """Per perishable product: what will be lost if nothing is done (FEFO simulation)."""
    t = t or today()
    demand, booked = _weekday_demand(t), _booked(t)
    horizon_end = add_days(t, HORIZON_DAYS)
    lots_by_product: dict[str, list[Doc]] = {}
    for lot in crud.lots.rows.values():
        if lot["qtyRemaining"] > 0 and t < lot["expiresAt"] <= horizon_end:
            lots_by_product.setdefault(lot["productId"], [])
    for lot in crud.lots.rows.values():  # every lot of those products (later ones absorb demand too)
        if lot["productId"] in lots_by_product and lot["qtyRemaining"] > 0 and lot["expiresAt"] > t:
            lots_by_product[lot["productId"]].append({**lot})
    out = []
    for pid, lots in lots_by_product.items():
        product = crud.products.get(pid)
        if not product or not product["active"]:
            continue
        lots.sort(key=lambda l: (l["expiresAt"], l["receivedAt"]))
        start = {l["id"]: l["qtyRemaining"] for l in lots}
        lost, first_loss = 0.0, None
        for i in range(1, HORIZON_DAYS + 1):
            day = add_days(t, i)
            need = max(demand.get((pid, weekday_of(day)), 0), booked.get((pid, day), 0))
            for lot in lots:
                if need <= 0:
                    break
                if lot["expiresAt"] >= day and lot["qtyRemaining"] > 0:
                    take = min(lot["qtyRemaining"], need)
                    lot["qtyRemaining"] -= take
                    need -= take
            for lot in lots:
                if lot["expiresAt"] == day and lot["qtyRemaining"] > 1e-6:
                    lost += lot["qtyRemaining"]
                    first_loss = first_loss or day
                    lot["qtyRemaining"] = 0
        if lost <= 0:
            continue
        # What a client actually receives: the oldest lot still holding a real share of the stock (FEFO). Its date is
        # the honest "keeps until" to announce, and the last day the client can be delivered.
        big = [l for l in lots if start[l["id"]] >= 0.1 * lost] or lots
        sell_by = min(big[0]["expiresAt"], first_loss)
        out.append({
            "product": product,
            "lost": round3(lost),
            "lossValue": round2(lost * product["cost"]),
            "lossDate": first_loss,
            "sellBy": sell_by,
            "lotId": big[0]["id"] if start[big[0]["id"]] >= lost else None,
        })  # fmt: skip
    return sorted(out, key=lambda x: -x["lossValue"])


def _buyers(t: str) -> dict[str, Doc]:
    """Per client: number of recent sale orders, and per product how many of them + total qty."""
    since = add_days(t, -BASKET_DAYS)
    out: dict[str, Doc] = {}
    for o in crud.orders.rows.values():
        if o["status"] in ("draft", "cancelled") or not since <= o["deliveryDate"] < t:
            continue
        c = out.setdefault(o["clientId"], {"orders": 0, "products": {}})
        c["orders"] += 1
        seen: dict[str, float] = {}
        for line in o["lines"]:
            seen[line["productId"]] = seen.get(line["productId"], 0) + line["qty"]
        for pid, qty in seen.items():
            s = c["products"].setdefault(pid, {"n": 0, "qty": 0.0})
            s["n"] += 1
            s["qty"] += qty
    return out


def _gain(cands: list[Doc], d: float, cap: float) -> tuple[float, float]:
    """(expected € recovered, expected qty cleared) at discount d, clearing capped at `cap`."""
    take = sum(c["P"](d) * c["x"](d) for c in cands)
    scale = min(1.0, cap / take) if take > 0 else 0.0
    gain = 0.0
    for c in cands:
        x = c["x"](d) * scale
        cann = c["f"] * min(x, c["q"])
        gain += c["P"](d) * ((x - cann) * c["p"] * (1 - d) - cann * c["p"] * d)
    return gain, take * scale


def proposal() -> Doc:
    t = today()
    buyers = _buyers(t)
    offers, targets, analysis = [], {}, []
    losses = [x for x in expected_losses(t) if x["lossValue"] >= MIN_LOSS_VALUE]
    for n, loss in enumerate(losses, 1):
        p = loss["product"]
        oid = f"off_{n}"
        cands = []
        for client in crud.clients.rows.values():
            b = buyers.get(client["id"])
            stats = b and b["products"].get(p["id"])
            if not stats or client["status"] != "active" or clients.next_delivery_date(client) > loss["sellBy"]:
                continue
            f = stats["n"] / b["orders"]
            q = stats["qty"] / stats["n"]
            a = _logit(BASE_ACCEPT[0] + (BASE_ACCEPT[1] - BASE_ACCEPT[0]) * f)
            cands.append({
                "client": client, "f": f, "q": q, "p": pricing.price_for(client["id"], p["id"], q),
                "P": (lambda d, a=a: _sigmoid(a + B_SENSITIVITY * d)),
                "x": (lambda d, q=q: q * (1 + E_VOLUME * d)),
            })  # fmt: skip
        if not cands:
            analysis.append(_row(oid, loss, None, None, None, 0, 0, 0, "aucun client livrable à temps"))
            continue
        floor = DONATION_RATE * p["cost"]
        max_allowed = max(0.05, 1 - floor / p["price"])
        grid = [d for d in GRID if d <= max_allowed] or [0.05]
        # Who to call: best expected value at a mid discount, until they cover COVER × the loss.
        mid = grid[len(grid) // 2]
        cands.sort(key=lambda c: -c["P"](mid) * c["x"](mid) * c["p"])
        chosen, covered = [], 0.0
        for c in cands:
            if len(chosen) >= MAX_TARGETS_PER_OFFER or covered >= COVER * loss["lost"]:
                break
            chosen.append(c)
            covered += c["P"](mid) * c["x"](mid)
        gains = {d: _gain(chosen, d, loss["lost"]) for d in grid}
        best = max(grid, key=lambda d: gains[d][0])
        near = gains[best][0] - 0.1 * abs(gains[best][0])  # within 10 % of the best (also when it is negative)
        top = max(d for d in grid if d >= best and gains[d][0] >= near)
        if gains[best][0] <= 0:
            analysis.append(_row(oid, loss, None, None, round2(floor), len(chosen), 0, 0, "remise non rentable : mieux vaut donner"))
            continue
        pct, max_pct = round(best * 100), round(top * 100)
        offers.append({
            "offerId": oid, "productId": p["id"], **({"lotId": loss["lotId"]} if loss["lotId"] else {}),
            "qty": _whole(loss["lost"], p["unit"]), "expiresAt": loss["sellBy"], "discountPct": pct, "maxDiscountPct": max_pct,
        })  # fmt: skip
        for c in chosen:
            value = c["P"](best) * c["x"](best) * c["p"] * (1 - best)
            tg = targets.setdefault(c["client"]["id"], {"clientId": c["client"]["id"], "offers": []})
            tg["offers"].append((value, oid, f"{p['name']} dans {round(c['f'] * 100)} % de ses commandes (~{_qty(c['q'], p['unit'])})"))
        analysis.append(_row(oid, loss, pct, max_pct, round2(floor), len(chosen), *gains[best][::-1]))

    for tg in targets.values():
        tg["offers"] = sorted(tg["offers"], key=lambda o: -o[0])[:MAX_OFFERS_PER_CALL]
        tg["value"] = sum(o[0] for o in tg["offers"])
    top_value = max((tg["value"] for tg in targets.values()), default=1) or 1
    target_list = sorted(
        ({"clientId": tg["clientId"], "offerIds": [o[1] for o in tg["offers"]], "score": round(tg["value"] / top_value, 2),
          "reason": " ; ".join(o[2] for o in tg["offers"])} for tg in targets.values()),
        key=lambda tg: -tg["score"],
    )  # fmt: skip
    called = {oid for tg in target_list for oid in tg["offerIds"]}
    offers = [o for o in offers if o["offerId"] in called]  # an offer every candidate preferred to skip: nobody to call
    for a in analysis:
        if a["offerId"] not in called and not a.get("note"):
            a.update(note=f"pas d'appel : ses clients ont {MAX_OFFERS_PER_CALL} offres plus rentables", targets=0)
    return {
        "campaign": {
            "name": f"Déstockage calculé du {fr_day(t).removeprefix('le ')}",
            "offers": offers,
            "targets": target_list,
            "closeOthers": True,
        },
        "analysis": analysis,
        "totalLossValue": round2(sum(x["lossValue"] for x in losses)),
        "params": {
            "horizonDays": HORIZON_DAYS, "historyWeeks": HISTORY_WEEKS, "sensitivity": B_SENSITIVITY, "volumeElasticity": E_VOLUME,
            "baseAccept": list(BASE_ACCEPT), "donationRate": DONATION_RATE, "minLossValue": MIN_LOSS_VALUE,
        },
    }  # fmt: skip


def _whole(q: float, unit: str) -> float:
    """Offer quantities as people say them: whole dozens / pieces / boxes, kg to 0.5."""
    return float(max(1, round(q))) if unit in ("piece", "box", "dozen") else max(0.5, round(q * 2) / 2)


UNIT_FR = {"kg": "kg", "piece": "pièces", "box": "colis", "dozen": "douzaines", "liter": "litres"}


def _qty(q: float, unit: str) -> str:
    return f"{round(q, 1):g} {UNIT_FR.get(unit, unit)}".replace(".", ",")


def _row(oid: str, loss: Doc, pct, max_pct, floor, n: int, cleared: float, recovered: float, note: str | None = None) -> Doc:
    p = loss["product"]
    return {
        "offerId": oid,
        "productId": p["id"],
        "productName": p["name"],
        "unit": p["unit"],
        "expectedLoss": loss["lost"],
        "lossValue": loss["lossValue"],
        "lossDate": loss["lossDate"],
        "sellBy": loss["sellBy"],
        **({"discountPct": pct, "maxDiscountPct": max_pct, "floorPrice": floor} if pct is not None else {}),
        "targets": n,
        "expectedCleared": round3(cleared),
        "expectedRecovered": round2(recovered),
        **({"note": note} if note else {}),
    }
