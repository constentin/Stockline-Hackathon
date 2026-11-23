"""Demo scenario: a surplus arrival of short-dated lots, to have real clearance work for the outbound agent.

POST /api/admin/demo/short-dated  (also `make demo-stock`; run it again after `make reset`, the data pack is untouched)

Picks the best-selling perishable products (shelf life ≤ 9 days, by revenue over the last 28 days) and adds to each one
lot received earlier, expiring in 1 to 3 days, of `factor` × the average daily demand, on top of the stock already
there: the total is more than normal demand can absorb before the dates run out, so without action some of it ends up
as waste (FEFO ships these lots first; the overflow then hits them or the next lots).
"""

from __future__ import annotations

from app import crud
from app.core.activity import Ctx, log_event, today
from app.core.db import Doc, transactional
from app.core.errors import conflict
from app.core.utils import add_days, fr_day, fr_num, round3
from app.services import stock

NOTE = "Arrivage en surplus (scénario de démo)"
WINDOW_DAYS = 28
# (days before expiry, × average daily demand): the shorter the date, the smaller the lot, all hard to sell in time
PLAN = [(1, 1.2), (2, 1.6), (2, 1.4), (3, 2.0), (3, 1.8), (1, 1.0), (2, 1.5), (3, 1.7)]


def _daily_demand() -> dict[str, float]:
    t = today()
    since = add_days(t, -WINDOW_DAYS)
    qty: dict[str, float] = {}
    for o in crud.orders.rows.values():
        if o["status"] in ("draft", "cancelled") or not since <= o["deliveryDate"] < t:
            continue
        for line in o["lines"]:
            qty[line["productId"]] = qty.get(line["productId"], 0) + line["qty"]
    return {pid: q / WINDOW_DAYS for pid, q in qty.items()}


def _rounded(qty: float, unit: str) -> float:
    return float(max(1, round(qty))) if unit in ("piece", "box", "dozen") else round(qty, 1)


@transactional
def add_short_dated_surplus(ctx: Ctx, count: int = 6) -> list[Doc]:
    """Add the surplus lots; refused if the scenario already ran today (it would double the surplus)."""
    t = today()
    if crud.stock_movements.filter(lambda m: m.get("note") == NOTE and m["at"][:10] == t):
        raise conflict("already_applied", "Le scénario de surplus a déjà été appliqué aujourd'hui (make reset pour repartir de zéro)")
    demand = _daily_demand()
    candidates = [
        p for p in crud.products.rows.values()
        if p["active"] and p["shelfLifeDays"] <= 9 and demand.get(p["id"], 0) > 0
    ]  # fmt: skip
    candidates.sort(key=lambda p: -demand[p["id"]] * p["price"])
    created = []
    for p, (days_left, factor) in zip(candidates[: max(1, min(count, len(PLAN)))], PLAN):
        days_left = min(days_left, p["shelfLifeDays"])
        expires = add_days(t, days_left)
        received = add_days(expires, -p["shelfLifeDays"])
        qty = _rounded(demand[p["id"]] * factor, p["unit"])
        lot = {
            "id": crud.lots.next_id("lot_"),
            "productId": p["id"],
            **({"supplierId": p["mainSupplierId"]} if p.get("mainSupplierId") else {}),
            "qtyReceived": qty,
            "qtyRemaining": qty,
            "receivedAt": received,
            "expiresAt": expires,
        }
        crud.lots.put(lot)
        stock._write_movement({"productId": p["id"], "lotId": lot["id"], "qty": qty, "reason": "adjustment", "refType": "manual", "note": NOTE})
        log_event(ctx, "stock.adjustment", "stock", p["id"],
                  f"Surplus : +{fr_num(qty)} {p['unit']} de {p['name']} (lot {lot['id']}, périme {fr_day(expires)})")  # fmt: skip
        created.append({
            "lotId": lot["id"],
            "productId": p["id"],
            "productName": p["name"],
            "unit": p["unit"],
            "qty": qty,
            "expiresAt": expires,
            "dailyDemand": round3(demand[p["id"]]),
        })  # fmt: skip
    return created
