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


# ───────── Scenario 2: a higher loss rate (10 % of revenue) ─────────

LOSS_NOTE = "DLC dépassée : destruction (scénario de démo : pertes portées à 10 % du CA)"
LOSS_SHELF_LIFE_MAX = 14  # perishable products only


@transactional
def raise_losses(ctx: Ctx, target_rate: float = 0.10) -> Doc:
    """Bring the waste of the last 12 months to `target_rate` × revenue (at purchase cost), consistently:
    on every perishable lot that expired in that year, the company received MORE and destroyed the extra the day
    after the expiry date — the lot's received quantity, its receipt movement and its purchase-order line grow by the
    same amount, and a waste movement records the destruction. Nothing that happened after (sales, stock today)
    changes. Once only (make reset to go back to the data pack)."""
    t = today()
    since = add_days(t, -365)
    if crud.stock_movements.filter(lambda m: m.get("note") == LOSS_NOTE):
        raise conflict("already_applied", "Le scénario de pertes a déjà été appliqué (make reset pour repartir de zéro)")
    products = crud.products.rows
    revenue = sum(
        (line.get("preparedQty") or line["qty"]) * line["unitPrice"]
        for o in crud.orders.rows.values() if o["status"] in ("delivered", "invoiced") and since <= o["deliveryDate"] < t
        for line in o["lines"]
    )  # fmt: skip
    cost = lambda pid: (products.get(pid) or {}).get("cost", 0)  # noqa: E731
    wasted = sum(abs(m["qty"]) * cost(m["productId"]) for m in crud.stock_movements.rows.values()
                 if m.get("reason") == "waste" and since <= m["at"][:10] < t)  # fmt: skip
    gap = target_rate * revenue - wasted
    before = {"revenue": round(revenue, 2), "wasteCost": round(wasted, 2), "rate": round(wasted / revenue, 4) if revenue else 0}
    if gap <= 0:
        return {"before": before, "after": before, "lots": 0, "added": 0}
    lots = [
        lot for lot in crud.lots.rows.values()
        if since <= lot["expiresAt"] < t and lot["qtyReceived"] > 0
        and (products.get(lot["productId"]) or {}).get("shelfLifeDays", 99) <= LOSS_SHELF_LIFE_MAX
    ]  # fmt: skip
    base = sum(lot["qtyReceived"] * cost(lot["productId"]) for lot in lots)
    share = gap / base if base else 0
    receipts = {m["lotId"]: m for m in crud.stock_movements.rows.values() if m.get("reason") == "receipt" and m.get("lotId")}
    added, touched = 0.0, 0
    for lot in sorted(lots, key=lambda x: x["expiresAt"]):
        p = products[lot["productId"]]
        extra = _rounded(lot["qtyReceived"] * share, p["unit"]) if p["unit"] in ("piece", "box", "dozen") else round(lot["qtyReceived"] * share, 2)
        if extra <= 0 or added >= gap:
            continue
        crud.lots.put({**lot, "qtyReceived": round3(lot["qtyReceived"] + extra)})
        if (r := receipts.get(lot["id"])) is not None:
            crud.stock_movements.put({**r, "qty": round3(r["qty"] + extra)})
        if lot.get("purchaseOrderId") and (po := crud.purchase_orders.get(lot["purchaseOrderId"])):
            lines = [dict(line) for line in po["lines"]]
            for line in lines:
                if line["productId"] == lot["productId"]:
                    line["qty"] = round3(line["qty"] + extra)
                    line["receivedQty"] = round3((line.get("receivedQty") or 0) + extra)
                    po = {**po, "lines": lines, "totalHt": round(po.get("totalHt", 0) + extra * line["unitPrice"], 2)}
                    break
            crud.purchase_orders.put(po)
        crud.stock_movements.put({
            "id": crud.stock_movements.next_id("mov_"), "at": f"{add_days(lot['expiresAt'], 1)}T03:00:00.000Z",
            "productId": lot["productId"], "lotId": lot["id"], "qty": -extra, "reason": "waste", "refType": "manual", "note": LOSS_NOTE,
        })  # fmt: skip
        added += extra * p["cost"]
        touched += 1
    after_cost = wasted + added
    log_event(ctx, "stock.adjustment", "stock", "waste",
              f"Scénario de démo : pertes des 12 derniers mois portées de {before['rate'] * 100:.1f} % à "
              f"{after_cost / revenue * 100:.1f} % du chiffre d'affaires ({touched} lots)")  # fmt: skip
    return {"before": before, "after": {"revenue": round(revenue, 2), "wasteCost": round(after_cost, 2), "rate": round(after_cost / revenue, 4)},
            "lots": touched, "added": round(added, 2)}  # fmt: skip
