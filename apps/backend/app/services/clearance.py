"""Clearance model (the team's model, « Décider des promotions sur les stocks périssables », see DECISIONS.md).

GET /api/campaigns/proposal → a campaign in the IMPORT format (POST /api/campaigns/import) + the analysis behind it.
GET /api/campaigns/backtest → the forecast's error on past weeks (WAPE), against « the previous week ».

Six operations:
1. HISTORY   deliveries (orders delivered / invoiced) per client × product × day. Commitments (confirmed / prepared
             orders) are kept apart: they are not history, they are taken from the stock AND from the demand.
2. DEMAND    calendar analogues: for each day ahead, the same day 2…17 weeks ago (16 windows that start on the same
             weekday and end before today), days without delivery counted as 0, weights halving every 8 weeks:
                 D̂ = Σ wᵢ·Dᵢ / Σ wᵢ
             + seasonal analogues (51–53 and 103–105 weeks ago, fixed weights 0.5 / 0.25) only when the recent
               history is thin (fewer than 4 of the 16 windows with a purchase) and they hold purchases;
             × rhythm adjustment: a client silent for more than 2 × his usual interval between purchases is forecast
               at 2·interval / silence of his usual demand;
             no analogue with a purchase → no forecast (the client is not a candidate, his demand is 0).
3. STOCK     free stock of a lot S_ℓ = quantity − reservations (confirmed / prepared orders take the oldest lots,
             FEFO); open demand = forecast − what the client already ordered; allocated day by day to the lots that
             expire first (a lot can be delivered up to the day before its DLC).
4. RISK      Q_r = max(0, S_ℓ − N_ℓ), R = Q_r / S_ℓ   (a share of a point forecast, not a probability).
5. DISCOUNT  d* = 40 % × λ × (1 + ε_c) × R × U(J),  U(J) = 0.5 + 0.5 / (1 + e^{3(J − 2)}),
             J = days until the LAST delivery admissible for this client (one of his delivery days, a route serving
             his zone, at least the day before the DLC);
             d = min(d*, campaign ceiling, economic ceiling = 1 − (c + A) / p₀);
             p = p₀ · (1 − d) rounded to the cent (so the announced discount is the effective one: 10.625 % in the
             deck's example for d* = 10.74 %).
6. OUTPUT    offers ranked by the scenario contribution
                 G = f · [ q·(p − c − A) − k·max(0, p₀ − c − A) ] − F
             f = min(1, B / q)   B = the client's open demand until his last admissible delivery, q = quantity offered
             k = q · (1 − R)     the part of the offer that would have sold at the normal price anyway
             c = variable cost of the sale (not the purchase cost: sunk), A = best alternative per unit (donation),
             F = cost of one call (per client). Offers with G ≤ 0 are dropped; per lot the quantities offered add up
             to the surplus Q_r at most; each client gets his 4 best offers, clients are called by Σ G − F.

LEARNING λ and ε only move through randomised paired comparisons (arms λ ± 0.05 / ε ± 0.025, kept on the offers):
the normalised contribution of the two offers of a pair is compared once both calls are over; λ moves by 0.05 after
≥ 8 pairs over ≥ 2 campaigns, ε_c by 0.025 after ≥ 12 pairs over ≥ 3 campaigns, only if the median difference is
beyond ±2 % with ≥ 2/3 of the pairs of the same sign. Until then λ = 0.60 and ε = 0.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from datetime import date

from app import crud
from app.core.activity import today
from app.core.db import Doc
from app.core.utils import add_days, fr_day, round2, round3, weekday_of
from app.services import clients, pricing, routing

# ── Parameters (DECISIONS.md §3: from the deck, or chosen where the deck is silent) ──
D_MAX = 0.40  # deck: commercial ceiling, scales the whole formula
LAMBDA_0 = 0.60  # deck: initial aggressiveness
HALF_LIFE_WEEKS = 8  # deck: recency half-life
ANALOGUES = 16  # deck: up to 16 windows
SEASONAL = ((51, 52, 53), (103, 104, 105))  # deck: seasonal analogues (weeks back)
SEASONAL_WEIGHTS = (0.5, 0.25)  # ours: the deck's half-life would give them 1 % and 0.01 %
SEASONAL_IF_FEWER_THAN = 4  # ours: « conditional » = the recent history has fewer than 4 windows with a purchase
RHYTHM_FACTOR = 2.0  # ours: silent for more than 2 × his usual interval → forecast scaled down
HORIZON_DAYS = 8  # lots whose DLC is within 8 days
CAMPAIGN_CEILING = 0.40  # « plafond de campagne », per campaign (here: the commercial ceiling)
VARIABLE_COST_RATE = 0.0  # c, share of the price (preparation, delivery…): unknown in the ERP
ALTERNATIVE_RATE = 0.6  # A = 0.6 × purchase cost: donation value (not demonstrated, to confirm)
CONTACT_COST = 1.0  # F, € per call (agent + phone): an estimate
MIN_DISCOUNT_PCT = 5.0  # below, nobody calls that an offer: the offer is dropped (not raised)
MIN_LOSS_VALUE = 30.0  # € at cost: a smaller surplus is not worth a call
MAX_TARGETS_PER_LOT = 8
MAX_OFFERS_PER_CALL = 4  # a phone call, not a catalogue
# learning (deck)
LAMBDA_ARM, EPS_ARM = 0.05, 0.025
LAMBDA_MIN_PAIRS, LAMBDA_MIN_CAMPAIGNS = 8, 2
EPS_MIN_PAIRS, EPS_MIN_CAMPAIGNS = 12, 3
DECISION_MEDIAN, SIGN_AGREEMENT = 0.02, 2 / 3
LAMBDA_BOUNDS, EPS_BOUNDS = (0.2, 1.0), (-0.5, 0.5)  # ours: keep the learning in a sane range
DEMAND_STATUSES = ("delivered", "invoiced")  # deck: « commandes facturées / livrées pour la demande »
COMMITTED_STATUSES = ("confirmed", "prepared")
OUTCOMES = ("accepted", "declined", "callback", "no_answer", "do_not_call")


def urgency(j: int) -> float:
    """U(J) = 0.5 + 0.5 / (1 + e^{3(J−2)}): 97.6 % the day before, 75 % at 2 days, 52.4 % at 3 days, → 50 %."""
    return 0.5 + 0.5 / (1 + math.exp(3 * (j - 2)))


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(b[:10]) - date.fromisoformat(a[:10])).days


# ───────── 1. History ─────────


class History:
    """Deliveries per (client, product): {day: qty}, and the purchase days (rhythm)."""

    def __init__(self, until: str):
        self.daily: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for o in crud.orders.rows.values():
            if o["status"] in DEMAND_STATUSES and o["deliveryDate"] < until:
                for line in o["lines"]:
                    self.daily[(o["clientId"], line["productId"])][o["deliveryDate"]] += line.get("preparedQty") or line["qty"]

    def usual_qty(self, key: tuple[str, str], t: str, weeks: int = 24) -> float:
        since = add_days(t, -7 * weeks)
        qs = [q for d, q in self.daily.get(key, {}).items() if since <= d < t and q > 0]
        return sum(qs) / len(qs) if qs else 0.0


# ───────── 2. Demand: calendar analogues ─────────


def forecast(h: History, key: tuple[str, str], t: str, horizon: int = HORIZON_DAYS, seasonal: bool = True, rhythm: bool = True) -> list[float] | None:
    """Expected deliveries for days t+1 … t+horizon (index 0 = tomorrow), or None (no analogue with a purchase)."""
    days = h.daily.get(key)
    if not days:
        return None
    first = max(1, math.ceil((horizon + 1) / 7))  # windows must end before t
    windows = [(w, 0.5 ** (w / HALF_LIFE_WEEKS)) for w in range(first, first + ANALOGUES)]
    recent_hits = sum(1 for w, _ in windows if any(days.get(add_days(t, k - 7 * w), 0) > 0 for k in range(1, horizon + 1)))
    if seasonal and recent_hits < SEASONAL_IF_FEWER_THAN:  # thin recent history: seasonal analogues, if they hold purchases
        for weeks, weight in zip(SEASONAL, SEASONAL_WEIGHTS):
            for w in weeks:
                if any(days.get(add_days(t, k - 7 * w), 0) > 0 for k in range(1, horizon + 1)):
                    windows.append((w, weight / len(weeks)))
    if recent_hits == 0 and len(windows) == ANALOGUES:
        return None  # no relevant analogue: forecast unavailable
    total_w = sum(wt for _, wt in windows)
    daily = [sum(wt * days.get(add_days(t, k - 7 * w), 0.0) for w, wt in windows) / total_w for k in range(1, horizon + 1)]
    # rhythm: a client silent for much longer than usual buys less
    bought = sorted(d for d, q in days.items() if q > 0 and d < t)
    if rhythm and len(bought) >= 3:
        gaps = sorted(_days(a, b) for a, b in zip(bought[-12:], bought[-11:]))
        usual_gap = gaps[len(gaps) // 2]
        silence = _days(bought[-1], t)
        if usual_gap > 0 and silence > RHYTHM_FACTOR * usual_gap:
            daily = [x * RHYTHM_FACTOR * usual_gap / silence for x in daily]
    return daily


def committed(t: str, until: str) -> dict[tuple[str, str], list[tuple[str, float]]]:
    """Orders already placed (confirmed / prepared), delivered after today: (client, product) → [(date, qty)]."""
    out: dict[tuple[str, str], list[tuple[str, float]]] = defaultdict(list)
    for o in crud.orders.rows.values():
        if o["status"] in COMMITTED_STATUSES and t < o["deliveryDate"] <= until:
            for line in o["lines"]:
                out[(o["clientId"], line["productId"])].append((o["deliveryDate"], line.get("preparedQty") or line["qty"]))
    return out


def open_demand(daily: list[float], booked: list[tuple[str, float]], t: str, until: str) -> float:
    """Demand from tomorrow until `until` not yet covered by an order: max(0, Σ forecast − ordered)."""
    n = max(0, min(len(daily), _days(t, until)))
    return max(0.0, sum(daily[:n]) - sum(q for d, q in booked if d <= until))


# ───────── 3 + 4. Stock and risk per lot ─────────


def risks(t: str | None = None, h: History | None = None, forecasts: dict | None = None) -> list[Doc]:
    """Per lot with a DLC within HORIZON_DAYS: free stock S, open demand allocated N (FEFO, day by day), Q_r, R."""
    t = t or today()
    h = h or History(t)
    forecasts = forecasts if forecasts is not None else {k: forecast(h, k, t) for k in h.daily}
    horizon_end = add_days(t, HORIZON_DAYS)
    booked = committed(t, add_days(t, 60))
    reserved: dict[str, float] = defaultdict(float)
    for (_, pid), rows in booked.items():
        reserved[pid] += sum(q for _, q in rows)
    lots_by_product: dict[str, list[Doc]] = defaultdict(list)
    for lot in crud.lots.rows.values():
        if lot["qtyRemaining"] > 0 and lot["expiresAt"] > t:
            lots_by_product[lot["productId"]].append({**lot})
    out = []
    for pid, lots in lots_by_product.items():
        product = crud.products.get(pid)
        if not product or not product["active"] or not any(l["expiresAt"] <= horizon_end for l in lots):
            continue
        lots.sort(key=lambda l: (l["expiresAt"], l["receivedAt"]))
        r = reserved.get(pid, 0.0)  # reservations take the oldest lots (FEFO): what is left is free
        for lot in lots:
            take = min(r, lot["qtyRemaining"])
            lot["free"], lot["sold"] = lot["qtyRemaining"] - take, 0.0
            r -= take
        clients_of = [(c, f) for (c, p), f in forecasts.items() if p == pid and f]
        prev = {c: 0.0 for c, _ in clients_of}
        for k in range(1, HORIZON_DAYS + 1):
            day, need = add_days(t, k), 0.0
            for c, f in clients_of:
                cum = open_demand(f, booked.get((c, pid), []), t, day)
                need += cum - prev[c]
                prev[c] = cum
            for lot in lots:  # deliverable up to the day before the DLC
                if need <= 1e-9:
                    break
                if lot["expiresAt"] > day and lot["free"] - lot["sold"] > 0:
                    take = min(lot["free"] - lot["sold"], need)
                    lot["sold"] += take
                    need -= take
        for lot in lots:
            if lot["expiresAt"] > horizon_end or lot["free"] <= 0:
                continue
            q_r = max(0.0, lot["free"] - lot["sold"])
            if q_r > 1e-6:
                out.append({"product": product, "lot": lot, "free": round3(lot["free"]), "expectedSales": round3(lot["sold"]),
                            "surplus": round3(q_r), "R": q_r / lot["free"], "lossValue": round2(q_r * product["cost"])})  # fmt: skip
    return sorted(out, key=lambda x: -x["lossValue"])


# ───────── 5. Discount ─────────


def last_admissible_delivery(client: Doc, dlc: str, t: str) -> str | None:
    """The last day before the DLC when this client can be delivered: one of his delivery days, a route serving his
    zone. None when there is none."""
    days = client.get("deliveryDays") or []
    for i in range(_days(t, dlc) - 1, 0, -1):
        d = add_days(t, i)
        if (not days or weekday_of(d) in days) and routing.zone_served_on(client["zone"], d):
            return d
    return None


def price_terms(p0: float, cost: float, r: float, j: int, lam: float, eps: float) -> Doc:
    """d*, the ceilings, the discount kept, the price rounded to the cent and the effective discount."""
    c, a = VARIABLE_COST_RATE * p0, ALTERNATIVE_RATE * cost
    d_star = D_MAX * lam * (1 + eps) * r * urgency(j)
    economic = max(0.0, 1 - (c + a) / p0) if p0 > 0 else 0.0
    d = max(0.0, min(d_star, CAMPAIGN_CEILING, economic))
    p = round2(p0 * (1 - d))
    return {"dStar": d_star, "d": d, "economic": economic, "price": p, "pct": round((1 - p / p0) * 100, 3) if p0 else 0.0,
            "maxPct": math.floor(min(D_MAX, CAMPAIGN_CEILING, economic) * 1000) / 10, "c": c, "a": a}  # fmt: skip


def contribution(q: float, b: float, r: float, p: float, p0: float, c: float, a: float) -> float:
    """G without F (F is per call): f · [q(p − c − A) − k·max(0, p₀ − c − A)], f = min(1, B/q), k = q(1 − R)."""
    if q <= 0:
        return 0.0
    f = min(1.0, b / q)
    return f * (q * (p - c - a) - q * (1 - r) * max(0.0, p0 - c - a))


# ───────── Learning: λ and ε by paired comparisons ─────────


def _decide(diffs: list[float]) -> int:
    """+1 (raise), −1 (lower) or 0 (no update): median beyond ±2 % and ≥ 2/3 of the pairs of the same sign."""
    if not diffs:
        return 0
    s = sorted(diffs)
    med = s[len(s) // 2] if len(s) % 2 else (s[len(s) // 2 - 1] + s[len(s) // 2]) / 2
    if abs(med) <= DECISION_MEDIAN:
        return 0
    sign = 1 if med > 0 else -1
    return sign if sum(1 for x in diffs if x * sign > 0) / len(diffs) >= SIGN_AGREEMENT else 0


def _normalised_contribution(offer: Doc, call: Doc, product: Doc) -> float:
    """What the offer brought, per € of normal value offered (0 when not taken)."""
    e = offer["experiment"]
    p0, r = e["usualPrice"], e["riskShare"]
    c, a = VARIABLE_COST_RATE * p0, ALTERNATIVE_RATE * product["cost"]
    got = 0.0
    for line in call.get("accepted") or []:
        if line["offerId"] == offer["id"]:
            got += line["qty"] * (line["unitPrice"] - c - a) - line["qty"] * (1 - r) * max(0.0, p0 - c - a)
    return got / (offer["qty"] * p0) if offer["qty"] * p0 > 0 else 0.0


def learned() -> Doc:
    """λ and ε_c replayed from the campaigns' paired experiments, oldest campaign first."""
    lam, eps = LAMBDA_0, defaultdict(float)
    acc_l: list[tuple[str, float]] = []
    acc_e: dict[str, list[tuple[str, float]]] = defaultdict(list)
    updates = []
    for camp in sorted(crud.campaigns.rows.values(), key=lambda c: c["createdAt"]):
        calls = [c for c in crud.campaign_calls.rows.values() if c["campaignId"] == camp["id"]]
        call_of = {oid: c for c in calls for oid in c["offerIds"]}
        pairs: dict[str, dict[str, Doc]] = defaultdict(dict)
        for o in camp["offers"]:
            if o.get("experiment"):
                pairs[o["experiment"]["pair"]][o["experiment"]["arm"]] = o
        for arms in pairs.values():
            if set(arms) != {"low", "high"}:
                continue
            ch, cl = call_of.get(arms["high"]["id"]), call_of.get(arms["low"]["id"])
            if not ch or not cl or ch["status"] not in OUTCOMES or cl["status"] not in OUTCOMES:
                continue  # not over yet
            prod = crud.products.get(arms["high"]["productId"]) or {"cost": 0}
            diff = _normalised_contribution(arms["high"], ch, prod) - _normalised_contribution(arms["low"], cl, prod)
            if arms["high"]["experiment"]["kind"] == "lambda":
                acc_l.append((camp["id"], diff))
            else:
                acc_e[ch["clientId"]].append((camp["id"], diff))
        if len(acc_l) >= LAMBDA_MIN_PAIRS and len({c for c, _ in acc_l}) >= LAMBDA_MIN_CAMPAIGNS:
            if move := _decide([d for _, d in acc_l]):
                lam = min(LAMBDA_BOUNDS[1], max(LAMBDA_BOUNDS[0], lam + move * LAMBDA_ARM))
                updates.append(f"λ {'+' if move > 0 else '−'}{LAMBDA_ARM} après {camp['id']}")
                acc_l = []
        for cid, acc in acc_e.items():
            if len(acc) >= EPS_MIN_PAIRS and len({c for c, _ in acc}) >= EPS_MIN_CAMPAIGNS and (move := _decide([d for _, d in acc])):
                eps[cid] = min(EPS_BOUNDS[1], max(EPS_BOUNDS[0], eps[cid] + move * EPS_ARM))
                updates.append(f"ε {cid} {'+' if move > 0 else '−'}{EPS_ARM} après {camp['id']}")
                acc_e[cid] = []
    return {"lambda": lam, "eps": dict(eps), "pendingLambdaPairs": len(acc_l), "updates": updates}


# ───────── 6. Output: who, how much, at what price ─────────


def _credit_ok(client: Doc, amount_ht: float, vat: float) -> bool:
    if client["creditLimit"] <= 0:
        return True
    return clients.client_balances(client["id"])["outstanding"] + amount_ht * (1 + vat) <= client["creditLimit"]


def proposal() -> Doc:
    t = today()
    h = History(t)
    forecasts = {k: forecast(h, k, t) for k in h.daily}
    booked = committed(t, add_days(t, 60))
    model = learned()
    lam, eps = model["lambda"], model["eps"]
    lots = [s for s in risks(t, h, forecasts) if s["lossValue"] >= MIN_LOSS_VALUE]
    offers, analysis = [], []
    n_offer = 0
    for n, s in enumerate(lots, 1):
        p, lot = s["product"], s["lot"]
        row = _row(f"lot_{n}", s)
        cands = []
        for client in crud.clients.rows.values():
            key = (client["id"], p["id"])
            f = forecasts.get(key)
            if not f or client["status"] != "active":
                continue  # 1. purchase history (a forecast exists) — and not blocked
            last = last_admissible_delivery(client, lot["expiresAt"], t)
            if not last:
                continue  # 3. no route can bring it before the DLC
            b = open_demand(f, booked.get(key, []), t, last)
            if b <= 1e-6:
                continue  # 2. no open demand before the last admissible delivery
            q0 = _whole(min(h.usual_qty(key, t) or b, s["surplus"]), p["unit"])
            p0 = pricing.price_for(client["id"], p["id"], q0)
            j = _days(t, last)
            terms = price_terms(p0, p["cost"], s["R"], j, lam, eps.get(client["id"], 0.0))
            if terms["pct"] < MIN_DISCOUNT_PCT:
                continue
            if not _credit_ok(client, q0 * terms["price"], p.get("vatRate", 0.055)):
                continue  # 3. credit limit
            g = contribution(q0, b, s["R"], terms["price"], p0, terms["c"], terms["a"])
            cands.append({"client": client, "B": b, "J": j, "p0": p0, "g": g, "terms": terms, "eps": eps.get(client["id"], 0.0)})
        # allocate the surplus to the best contributions (quantities add up to Q_r at most)
        cands.sort(key=lambda c: -c["g"])
        left, chosen = s["surplus"], []
        for c in cands:
            if left <= 1e-6 or len(chosen) >= MAX_TARGETS_PER_LOT:
                break
            key = (c["client"]["id"], p["id"])
            q = _whole(min(h.usual_qty(key, t) or c["B"], left), p["unit"])
            q = min(q, left) if p["unit"] in ("kg", "liter") else q
            g = contribution(q, c["B"], s["R"], c["terms"]["price"], c["p0"], c["terms"]["c"], c["terms"]["a"])
            if g <= 0 or q <= 0:
                continue  # the discount does not pay for the normal sales it displaces
            chosen.append({**c, "q": q, "g": g})
            left -= q
        if not chosen:
            note = "aucun client livrable à temps avec une demande ouverte" if not cands else "contribution négative : mieux vaut l'alternative (don)"
            analysis.append({**row, "note": note})
            continue
        for c in chosen:
            n_offer += 1
            offers.append({"offerId": f"off_{n_offer}", "productId": p["id"], "lotId": lot["id"], "qty": c["q"],
                           "expiresAt": lot["expiresAt"], "discountPct": c["terms"]["pct"], "maxDiscountPct": max(c["terms"]["pct"], c["terms"]["maxPct"]),
                           "_lot": row["offerId"], "_g": c["g"], "_c": c, "_R": s["R"], "_cost": p["cost"],
                           "_reason": f"{p['name']} : demande ouverte ~{_qty(c['B'], p['unit'])}, dernière tournée dans {c['J']} j, "
                                      f"remise {c['terms']['pct']:g} %"})  # fmt: skip
        pcts = sorted(c["terms"]["pct"] for c in chosen)
        offered = sum(c["q"] for c in chosen)
        js = min(c["J"] for c in chosen)
        row.update(discountPct=pcts[len(pcts) // 2], maxDiscountPct=max(c["terms"]["maxPct"] for c in chosen), targets=len(chosen),
                   expectedCleared=round3(offered), expectedRecovered=round2(sum(c["q"] * c["terms"]["price"] for c in chosen)),
                   contribution=round2(sum(c["g"] for c in chosen)), daysLeft=js, urgency=round(urgency(js), 3))  # fmt: skip
        analysis.append(row)

    # each client: his MAX_OFFERS_PER_CALL best contributions; called if Σ G − F > 0, best first
    per_client: dict[str, list[Doc]] = defaultdict(list)
    for o in offers:
        per_client[o["_c"]["client"]["id"]].append(o)
    targets = []
    for cid, items in per_client.items():
        items = sorted(items, key=lambda o: -o["_g"])[:MAX_OFFERS_PER_CALL]
        value = sum(o["_g"] for o in items) - CONTACT_COST
        if value > 0:
            targets.append({"clientId": cid, "offerIds": [o["offerId"] for o in items], "value": value,
                            "reason": " ; ".join(o["_reason"] for o in items)})  # fmt: skip
    called = {oid for tg in targets for oid in tg["offerIds"]}
    offers = [o for o in offers if o["offerId"] in called]
    _assign_experiments(offers, t, lam, eps)
    top = max((tg["value"] for tg in targets), default=1) or 1
    targets = sorted(({"clientId": tg["clientId"], "offerIds": tg["offerIds"], "score": round(tg["value"] / top, 2), "reason": tg["reason"]}
                      for tg in targets), key=lambda tg: -tg["score"])  # fmt: skip
    for a in analysis:  # a lot none of whose clients is called
        if not a.get("note") and not any(o["_lot"] == a["offerId"] for o in offers):
            a.update(note=f"pas d'appel : ses clients ont {MAX_OFFERS_PER_CALL} offres plus rentables ou une contribution trop faible", targets=0)
    return {
        "campaign": {"name": f"Déstockage calculé du {fr_day(t).removeprefix('le ')}", "offers": [_public(o) for o in offers],
                     "targets": targets, "closeOthers": True},  # fmt: skip
        "analysis": analysis,
        "totalLossValue": round2(sum(s["lossValue"] for s in lots)),
        "params": {
            "dMax": D_MAX, "lambda": lam, "lambdaInitial": LAMBDA_0, "halfLifeWeeks": HALF_LIFE_WEEKS, "analogues": ANALOGUES,
            "horizonDays": HORIZON_DAYS, "urgency": [round(urgency(j), 3) for j in range(1, 7)], "campaignCeiling": CAMPAIGN_CEILING,
            "variableCostRate": VARIABLE_COST_RATE, "alternativeRate": ALTERNATIVE_RATE, "contactCost": CONTACT_COST,
            "minDiscountPct": MIN_DISCOUNT_PCT, "minLossValue": MIN_LOSS_VALUE, "pendingLambdaPairs": model["pendingLambdaPairs"],
        },
    }  # fmt: skip


def _assign_experiments(offers: list[Doc], t: str, lam: float, eps: dict[str, float]) -> None:
    """Randomised pairs (seeded by the day: the same proposal twice gives the same arms). A client with ≥ 2 offers:
    his two best form an ε pair (ε ± 0.025); the other offers form λ pairs within their lot (λ ± 0.05). The arm
    changes the offer's discount; the pair and the arm are kept on the offer for the learning."""
    rng = random.Random(t)
    used: set[str] = set()
    by_client: dict[str, list[Doc]] = defaultdict(list)
    for o in offers:
        by_client[o["_c"]["client"]["id"]].append(o)
    pairs: list[tuple[str, list[Doc]]] = []
    for cid, items in sorted(by_client.items()):
        if len(items) >= 2:
            two = sorted(items, key=lambda o: -o["_g"])[:2]
            pairs.append(("eps", two))
            used.update(o["offerId"] for o in two)
    by_lot: dict[str, list[Doc]] = defaultdict(list)
    for o in offers:
        if o["offerId"] not in used:
            by_lot[o["_lot"]].append(o)
    for _, items in sorted(by_lot.items()):
        items = sorted(items, key=lambda o: -o["_g"])
        pairs += [("lambda", items[i : i + 2]) for i in range(0, len(items) - 1, 2)]
    for n, (kind, two) in enumerate(pairs, 1):
        arms = ["low", "high"]
        rng.shuffle(arms)
        for o, arm in zip(two, arms):
            c, sign = o["_c"], (1 if arm == "high" else -1)
            cid = c["client"]["id"]
            lam_o = lam + sign * LAMBDA_ARM if kind == "lambda" else lam
            eps_o = eps.get(cid, 0.0) + (sign * EPS_ARM if kind == "eps" else 0.0)
            terms = price_terms(c["p0"], o["_cost"], o["_R"], c["J"], lam_o, eps_o)
            o["discountPct"] = max(0.0, terms["pct"])
            o["maxDiscountPct"] = max(o["discountPct"], terms["maxPct"])
            o["experiment"] = {"kind": kind, "pair": f"p{n}", "arm": arm, "value": round(lam_o if kind == "lambda" else eps_o, 4),
                               "riskShare": round(o["_R"], 4), "usualPrice": c["p0"]}  # fmt: skip


def _public(o: Doc) -> Doc:
    return {k: v for k, v in o.items() if not k.startswith("_")}


# ───────── Backtest: is the forecast any good? ─────────


def backtest(weeks: int = 8, seasonal: bool = True, rhythm: bool = True) -> Doc:
    """WAPE of the 7-day forecast on the last `weeks` disjoint weeks (only data before each week), per unit, against
    the naive forecast « same as the previous week ». WAPE = Σ|forecast − actual| / Σ actual; bias = Σ(f − a) / Σ a."""
    t = today()
    h = History(t)
    units = {p["id"]: p["unit"] for p in crud.products.rows.values()}
    acc: dict[str, dict[str, float]] = defaultdict(lambda: {"abs": 0.0, "naive": 0.0, "actual": 0.0, "bias": 0.0})
    for w in range(1, weeks + 1):
        start = add_days(t, -7 * w)  # forecast made on `start` for start+1 … start+7
        for key, days in h.daily.items():
            f = forecast(h, key, start, 7, seasonal, rhythm)
            fc = sum(f) if f else 0.0
            actual = sum(days.get(add_days(start, k), 0.0) for k in range(1, 8))
            naive = sum(days.get(add_days(start, k - 7), 0.0) for k in range(1, 8))
            if fc == 0 and actual == 0 and naive == 0:
                continue
            u = acc[units.get(key[1], "?")]
            u["abs"] += abs(fc - actual)
            u["naive"] += abs(naive - actual)
            u["actual"] += actual
            u["bias"] += fc - actual
    return {
        "weeks": weeks,
        "seasonal": seasonal,
        "rhythm": rhythm,
        "byUnit": {u: {"wapeAnalogues": round(a["abs"] / a["actual"] * 100, 1), "wapePreviousWeek": round(a["naive"] / a["actual"] * 100, 1),
                       "biasPct": round(a["bias"] / a["actual"] * 100, 1), "actual": round(a["actual"], 1)}
                   for u, a in acc.items() if a["actual"] > 0},
    }  # fmt: skip


def _whole(q: float, unit: str) -> float:
    """Offer quantities as people say them: whole dozens / pieces / boxes, kg to 0.5."""
    return float(max(1, round(q))) if unit in ("piece", "box", "dozen") else max(0.5, round(q * 2) / 2)


UNIT_FR = {"kg": "kg", "piece": "pièces", "box": "colis", "dozen": "douzaines", "liter": "litres"}


def _qty(q: float, unit: str) -> str:
    return f"{round(q, 1):g} {UNIT_FR.get(unit, unit)}".replace(".", ",")


def _row(oid: str, s: Doc) -> Doc:
    p, lot = s["product"], s["lot"]
    return {
        "offerId": oid, "productId": p["id"], "productName": p["name"], "unit": p["unit"], "lotId": lot["id"],
        "freeStock": s["free"], "expectedSales": s["expectedSales"], "surplusShare": round(s["R"], 4),
        "expectedLoss": s["surplus"], "lossValue": s["lossValue"], "lossDate": lot["expiresAt"], "sellBy": lot["expiresAt"],
        "floorPrice": round2(ALTERNATIVE_RATE * p["cost"]), "targets": 0, "expectedCleared": 0.0, "expectedRecovered": 0.0,
    }  # fmt: skip
