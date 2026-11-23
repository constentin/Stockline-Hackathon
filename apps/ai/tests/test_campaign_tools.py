"""Outbound clearance-call tools: what they send to the ERP and what they give back to the model (offline)."""

import json

import httpx

from app.tools import TOOLS, all_tools, outbound_tools
from app.tools.erp import ErpClient
from app.tools.registry import ToolContext

ORDER = {
    "id": "ord_000042",
    "number": "CMD-2026-000042",
    "status": "draft",
    "client": {"name": "Brasserie Lambert"},
    "deliveryDate": "2026-11-25",
    "totalHt": 107.2,
    "lines": [{"id": "lin_1", "productId": "prd_0013", "productName": "Moule de bouchot", "qty": 40, "unit": "kg", "unitPrice": 2.68}],
    "warnings": [
        {"code": "expiring_soon", "message": "Moule : le lot expire le ven. 27 nov."},
        {"code": "credit_limit", "message": "Plafond de crédit dépassé"},
    ],
}


def ctx_with(handler) -> tuple[ToolContext, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(record))
    return ToolContext(erp=ErpClient("voice_agent", "Agent vocal", base_url="http://erp", http=http), actor="voice_agent"), seen


def test_tool_sets():
    inbound = {t.name for t in all_tools()}
    outbound = {t.name for t in outbound_tools()}
    assert "accept_offer" not in inbound  # inbound prompts (and their cache) are unchanged
    assert {"get_call_brief", "quote_offer", "accept_offer", "record_call_outcome", "confirm_order"} <= outbound
    assert "search" not in outbound


async def test_quote_sends_discount_and_keeps_it_small():
    ctx, seen = ctx_with(lambda r: httpx.Response(200, json={
        "offerId": "off_1", "productName": "Moule", "unit": "kg", "discountPct": 28, "usualPrice": 3.72, "unitPrice": 2.68,
        "qty": 40, "totalHt": 107.2, "remaining": 120, "deliveryDate": "2026-11-25", "expiresAt": "2026-11-27",
    }))  # fmt: skip
    out = await TOOLS["quote_offer"].run({"callId": "ccl_000001", "offerId": "off_1", "discountPct": 28, "qty": 40}, ctx)
    assert seen[0].url.path == "/api/campaign-calls/ccl_000001/quote"
    assert dict(seen[0].url.params) == {"offerId": "off_1", "pct": "28.0", "qty": "40.0"}
    assert out["unitPrice"] == 2.68 and "offerId" not in out


async def test_accept_drops_expected_expiry_warning():
    ctx, seen = ctx_with(lambda r: httpx.Response(200, json={"call": {"id": "ccl_000001"}, "order": ORDER}))
    out = await TOOLS["accept_offer"].run({"callId": "ccl_000001", "offerId": "off_1", "qty": 40}, ctx)
    assert seen[0].method == "POST" and json.loads(seen[0].content) == {"offerId": "off_1", "qty": 40.0}
    assert seen[0].headers["x-actor"] == "voice_agent"
    assert out["number"] == "CMD-2026-000042" and out["lines"][0]["lineId"] == "lin_1"
    assert [w["code"] for w in out["warnings"]] == ["credit_limit"]


async def test_refusal_reaches_the_model_in_french():
    ctx, _ = ctx_with(lambda r: httpx.Response(400, json={"error": {"code": "discount_too_high", "message": "Remise trop forte : 30 % au maximum"}}))
    try:
        await TOOLS["quote_offer"].run({"callId": "ccl_000001", "offerId": "off_1", "discountPct": 40}, ctx)
    except Exception as e:
        assert "30 % au maximum" in str(e)
    else:
        raise AssertionError("expected the ERP refusal")


async def test_record_outcome():
    ctx, seen = ctx_with(lambda r: httpx.Response(200, json={"id": "ccl_000001", "status": "callback", "callbackAt": "2026-11-26T15:00", "clientName": "X"}))
    out = await TOOLS["record_call_outcome"].run(
        {"callId": "ccl_000001", "outcome": "callback", "summary": "Rappeler après le service", "callbackAt": "2026-11-26T15:00"}, ctx
    )
    assert seen[0].method == "PATCH"
    assert json.loads(seen[0].content) == {"status": "callback", "summary": "Rappeler après le service", "callbackAt": "2026-11-26T15:00"}
    assert out == {"status": "callback", "callbackAt": "2026-11-26T15:00"}
    try:
        await TOOLS["record_call_outcome"].run({"callId": "ccl_000001", "outcome": "maybe", "summary": "?"}, ctx)
    except ValueError:
        pass
    else:
        raise AssertionError("outcome is a closed list")
