"""
Outbound clearance calls — scenario bench (text, no audio): the real agent, the real ERP, scripted client lines.

    uv run python -m app.eval_outbound              (from apps/ai; Docker: docker compose exec ai python -m app.eval_outbound)
    uv run python -m app.eval_outbound refus rappel (only these scenarios)
    uv run python -m app.eval_outbound --all        (also "ne_plus_appeler": it leaves a note on the client, `make reset` removes it)

Each scenario imports its own one-client campaign, plays the call (the agent speaks first), hangs up, then checks the
ERP: call status, order, discount within the max. It prints success, turns, model steps, tokens and latency, then
cancels the orders it created and closes the campaign. Results are appended to ./eval-outbound.jsonl (for DECISIONS.md;
in Docker, copy it out: docker compose cp ai:/app/apps/ai/eval-outbound.jsonl .).
"""

import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.agent import run_agent
from app.clients import get_llm_client
from app.schemas import ChatMessage, DoneEvent, ErrorEvent, TextEvent, ToolCallEvent
from app.services.outbound import OPENING_CUE, end_call, start_call
from app.tools import ErpClient, outbound_tools
from app.tools.erp import ErpError, enc

CLIENT = "cli_0065"  # Brasserie Lambert: delivered every day, buys mussels
OFFER = {"offerId": "off_1", "productId": "prd_0013", "lotId": "lot_023163", "qty": 120, "discountPct": 20, "maxDiscountPct": 30}


@dataclass
class Scenario:
    name: str
    lines: list[str]  # what the client says, in order
    expect_status: str
    expect_order: str | None = None  # order status, or None = no order
    min_discount: float | None = None  # negotiation: the agent must have gone beyond the opening discount
    said_any: tuple[str, ...] = ()  # the agent must say one of these words (lowercase)
    side_effects: bool = False  # leaves a lasting trace on the client: only with --all


SCENARIOS = [
    Scenario("accord", ["Ah oui pourquoi pas, mettez-m'en 20 kilos.", "Oui je confirme.", "Non c'est tout, merci, au revoir."],
             expect_status="accepted", expect_order="confirmed"),
    Scenario("negociation", ["C'est encore cher pour moi, vous pouvez faire mieux ?", "Bon d'accord, 30 kilos à ce prix-là.", "Oui, je confirme.",
                             "Non merci, rien d'autre. Au revoir."],
             expect_status="accepted", expect_order="confirmed", min_discount=20.01),
    Scenario("refus", ["Non merci, j'en ai assez cette semaine.", "Non vraiment, merci, au revoir."], expect_status="declined"),
    Scenario("rappel", ["Là je suis en plein coup de feu, rappelez-moi demain vers 15 heures."], expect_status="callback"),
    Scenario("robot", ["Attendez, vous êtes un robot ?", "Ah d'accord. Bon, non merci alors, au revoir."],
             expect_status="declined", said_any=("assistant", "assistante", "ia", "intelligence", "virtuelle")),
    Scenario("ne_plus_appeler", ["Arrêtez de m'appeler pour vos promotions, je ne veux plus être démarché."],
             expect_status="do_not_call", side_effects=True),
]  # fmt: skip


@dataclass
class Result:
    name: str
    ok: bool = False
    problems: list[str] = field(default_factory=list)
    turns: int = 0
    steps: int = 0
    tools: list[str] = field(default_factory=list)
    input_tokens: int = 0
    cache_read: int = 0
    output_tokens: int = 0
    first_token_ms: list[int] = field(default_factory=list)
    agent: list[str] = field(default_factory=list)


async def play(s: Scenario, erp: ErpClient) -> Result:
    r = Result(s.name)
    cid = f"cmp_eval_{s.name}_{int(time.time())}"
    imported = await erp.post("/api/campaigns/import", {"campaignId": cid, "name": f"Banc : {s.name}", "offers": [OFFER],
                                                        "targets": [{"clientId": CLIENT, "offerIds": ["off_1"], "score": 1}]})  # fmt: skip
    call_id = imported["calls"][0]["id"]
    try:
        call = await start_call(call_id, "Banc de test")
        llm, tools = get_llm_client(), outbound_tools()
        history = [ChatMessage(role="user", content=OPENING_CUE)]
        for line in [None, *s.lines]:
            if line is not None:
                history.append(ChatMessage(role="user", content=line))
            r.turns += 1
            t0, first, said = time.perf_counter(), None, ""
            before = sum(1 for m in history if m.role == "assistant")
            async for ev in run_agent(history, llm=llm, tools=tools, mode="outbound", actor_name="Banc de test", system_extra=call.prompt):
                match ev:
                    case TextEvent(delta=d):
                        first = first or int((time.perf_counter() - t0) * 1000)
                        said += d
                    case ToolCallEvent(call=c):
                        r.tools.append(c.name)
                    case DoneEvent(usage=u):
                        r.input_tokens += u.input_tokens or 0
                        r.cache_read += u.cache_read_input_tokens or 0
                        r.output_tokens += u.output_tokens or 0
                    case ErrorEvent(message=m):
                        r.problems.append(f"erreur agent : {m}")
            r.steps += sum(1 for m in history if m.role == "assistant") - before  # one assistant message per model call
            r.agent.append(said.strip())
            if first is not None:
                r.first_token_ms.append(first)
        await end_call(call, history, "Banc de test")  # hang up (records an outcome if the agent did not)

        # ── checks in the ERP ──
        c = await erp.get(f"/api/campaign-calls/{enc(call_id)}")
        if c["status"] != s.expect_status:
            r.problems.append(f"statut {c['status']} au lieu de {s.expect_status}")
        order = await erp.get(f"/api/orders/{enc(c['orderId'])}") if c.get("orderId") else None
        if (order["status"] if order else None) != s.expect_order:
            r.problems.append(f"commande {order['status'] if order else 'aucune'} au lieu de {s.expect_order or 'aucune'}")
        for line in c.get("accepted") or []:
            if line["discountPct"] > OFFER["maxDiscountPct"]:
                r.problems.append(f"remise {line['discountPct']} % au-delà du maximum")
            if s.min_discount and line["discountPct"] < s.min_discount:
                r.problems.append(f"pas de geste commercial (remise {line['discountPct']} %)")
        spoken = " ".join(r.agent).lower()
        words = set(re.findall(r"[a-zà-ÿ]+", spoken))
        if s.said_any and not words & set(s.said_any):
            r.problems.append(f"n'a dit aucun de {s.said_any}")
        if any(x in spoken for x in ("ccl_", "off_", "prd_", "lot_")):
            r.problems.append("a prononcé un identifiant technique")
        r.ok = not r.problems
        if order and order["status"] not in ("cancelled", "delivered", "invoiced"):
            await erp.post(f"/api/orders/{enc(order['id'])}/cancel")
    finally:
        await erp.patch(f"/api/campaigns/{enc(cid)}", {"status": "closed"})
    return r


async def main() -> None:
    names = [a for a in sys.argv[1:] if not a.startswith("--")]
    every = "--all" in sys.argv
    chosen = [s for s in SCENARIOS if (s.name in names if names else (every or not s.side_effects))]
    erp = ErpClient("voice_agent", "Banc de test")
    llm = get_llm_client()
    print(f"llm={llm.provider}:{llm.model} — {len(chosen)} scénario(s)\n")
    results = []
    for s in chosen:
        try:
            r = await play(s, erp)
        except ErpError as e:
            r = Result(s.name, problems=[f"ERP : {e}"])
        results.append(r)
        ft = sorted(r.first_token_ms)
        median = ft[len(ft) // 2] if ft else None
        print(f"{'OK ' if r.ok else 'KO '} {s.name:<16} {r.turns} tours, {r.steps} appels modèle, outils {r.tools}")
        print(f"    tokens entrée {r.input_tokens} (+ {r.cache_read} lus en cache), sortie {r.output_tokens}, 1er token médian {median} ms")
        for p in r.problems:
            print(f"    ✗ {p}")
        for i, said in enumerate(r.agent):
            print(f"    agent {i + 1} › {said}")
        print()
    passed = sum(r.ok for r in results)
    print(f"{passed}/{len(results)} réussis")
    out = Path.cwd() / "eval-outbound.jsonl"
    try:
        with out.open("a", encoding="utf-8") as f:
            for r in results:
                f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": llm.model, **r.__dict__}, ensure_ascii=False) + "\n")
        print(f"résultats ajoutés à {out}")
    except OSError as e:
        print(f"(résultats non écrits : {e})")


if __name__ == "__main__":
    asyncio.run(main())
