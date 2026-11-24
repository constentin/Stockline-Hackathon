"""
Outbound clearance calls — scenario bench (text, no audio): the real agent, the real ERP, scripted client lines.

    uv run python -m app.eval_outbound              (from apps/ai; Docker: docker compose exec ai python -m app.eval_outbound)
    uv run python -m app.eval_outbound refus rappel (only these scenarios)
    uv run python -m app.eval_outbound --all        (also "ne_plus_appeler": it leaves a note on the client, `make reset` removes it)
    uv run python -m app.eval_outbound --repeat 3   (each scenario 3 times: success rate, median and worst latency)
    uv run python -m app.eval_outbound --no-router  (without Jev: every turn on the big model, as before; to compare)
    LLM_FAST_MODEL=claude-sonnet-5-5 uv run …        (no small model: Sonnet everywhere)

Each scenario imports its own one-client campaign, plays the call (the agent speaks first) through the SAME path as
the voice session (Jev route → plan_turn → run_outbound with fallback), hangs up, then checks the ERP: call status,
order, quantities, discount within the max. "1er son" = client's words → first thing heard: the pre-recorded waiting
phrase when the model starts with a tool, else its first word (Jev routing included, without the head start it gets
in a real call). It cancels the orders it created and closes the campaign. Results are appended to ./eval-outbound.jsonl (for DECISIONS.md;
in Docker, copy it out: docker compose cp ai:/app/apps/ai/eval-outbound.jsonl .).
"""

import asyncio
import json
import random
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.clients import get_fast_llm_client, get_jev_client, get_llm_client
from app.schemas import ChatMessage, DoneEvent, ErrorEvent, TextEvent, ToolCallEvent
from app.services.outbound import OPENING_CUE, Plan, end_call, plan_turn, run_outbound, start_call
from app.services.router import classify
from app.tools import ErpClient
from app.tools.erp import ErpError, enc

CLIENT = "cli_0065"  # Brasserie Lambert: delivered every day, buys mussels, pink shrimps and whelks
# Three offers, like a real call: the opening must present them all and let the client choose.
OFFERS = [
    {"offerId": "off_1", "productId": "prd_0027", "qty": 40, "expiresAt": "2026-11-26", "discountPct": 15, "maxDiscountPct": 25},
    {"offerId": "off_2", "productId": "prd_0013", "qty": 120, "expiresAt": "2026-11-27", "discountPct": 20, "maxDiscountPct": 30},
    {"offerId": "off_3", "productId": "prd_0022", "qty": 30, "expiresAt": "2026-11-27", "discountPct": 20, "maxDiscountPct": 30},
]
OPENING_WORDS = ("crevette", "moule", "bulot")  # one per offer: each must be named in the opening
MAX_WORDS = 30  # per reply after the opening (the prompt asks for 25)
MAX_WORDS_OPENING = 55  # the prompt asks for 45


@dataclass
class Scenario:
    name: str
    lines: list[str]  # what the client says, in order
    expect_status: str
    expect_order: str | None = None  # order status, or None = no order
    min_discount: float | None = None  # negotiation: the agent must have gone beyond the opening discount
    said_any: tuple[str, ...] = ()  # the agent must say one of these words (lowercase)
    expect_qty: dict[str, float] | None = None  # offerId → quantity accepted at the end
    side_effects: bool = False  # leaves a lasting trace on the client: only with --all


SCENARIOS = [
    Scenario("accord", ["Les crevettes roses oui, mettez-m'en 10 kilos.", "Oui c'est bon.", "Non c'est tout, merci, au revoir."],
             expect_status="accepted", expect_order="confirmed"),
    Scenario("plusieurs", ["Les moules et les bulots m'intéressent. 30 kilos de moules et 10 de bulots.", "Oui, allez-y.",
                           "Merci, au revoir."],
             expect_status="accepted", expect_order="confirmed"),
    Scenario("negociation", ["Les moules pourquoi pas, mais c'est encore cher, vous pouvez faire mieux ?", "Bon d'accord, 30 kilos à ce prix-là.",
                             "Oui, je confirme.", "Non merci, rien d'autre. Au revoir."],
             expect_status="accepted", expect_order="confirmed", min_discount=20.01),
    Scenario("refus", ["Non merci, rien de tout ça cette semaine.", "Non vraiment, merci, au revoir."], expect_status="declined"),
    Scenario("rappel", ["Là je suis en plein coup de feu, rappelez-moi demain vers 15 heures."], expect_status="callback"),
    Scenario("robot", ["Attendez, vous êtes un robot ?", "Ah d'accord. Bon, non merci alors, au revoir."],
             expect_status="declined", said_any=("assistant", "assistante", "ia", "intelligence", "virtuelle")),
    Scenario("change_d_avis", ["Les crevettes, 10 kilos.", "Euh attendez, mettez plutôt 15 kilos.", "Oui c'est bon.", "Merci, au revoir."],
             expect_status="accepted", expect_order="confirmed", expect_qty={"off_1": 15}),
    Scenario("oui_ambigu", ["Allez, 20 kilos de moules.", "Oui mais attendez, c'est livré quand déjà ?", "Ok parfait, validez.", "Au revoir."],
             expect_status="accepted", expect_order="confirmed", expect_qty={"off_2": 20}),
    Scenario("transcription_abimee", ["Les mou de bouchot là, mettez m'en trente kilo.", "Oui allé y.", "Au revoir."],
             expect_status="accepted", expect_order="confirmed", expect_qty={"off_2": 30}),
    Scenario("hesitation", ["Je sais pas trop… les bulots ils se gardent jusqu'à quand ?", "Bon, 10 kilos de bulots alors.", "Oui.", "Au revoir."],
             expect_status="accepted", expect_order="confirmed", expect_qty={"off_3": 10}),
    Scenario("oui_a_une_question", ["Les crevettes, 10 kilos.", "Oui c'est bon, mettez-moi aussi 20 kilos de moules.", "Oui, je valide.", "Au revoir."],
             expect_status="accepted", expect_order="confirmed", expect_qty={"off_1": 10, "off_2": 20}),
    Scenario("incomprehensible", ["Le chat des dés vingt pour la cave.", "Vingt kilos de moules.", "Oui c'est bon.", "Au revoir."],
             expect_status="accepted", expect_order="confirmed", expect_qty={"off_2": 20},
             said_any=("pardon", "répéter", "entendu", "compris", "préciser", "redire", "excusez", "saisi")),
    Scenario("deux_fois_flou", ["Mettez-moi des... comment... les machins.", "Euh ouais le truc là, vous voyez.", "Les bulots, dix kilos.",
                                "Oui.", "Au revoir."],
             expect_status="accepted", expect_order="confirmed", expect_qty={"off_3": 10}),
    Scenario("produit_manquant", ["Mes paiements, 20 kilos.", "Les moules.", "Oui c'est bon.", "Au revoir."],
             expect_status="accepted", expect_order="confirmed", expect_qty={"off_2": 20}),
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
    first_sound_ms: list[int] = field(default_factory=list)  # filler or first word, per client turn
    routes: list[str] = field(default_factory=list)  # per turn: intent / action / model
    agent: list[str] = field(default_factory=list)
    words: list[int] = field(default_factory=list)  # words per reply
    repeats: list[str] = field(default_factory=list)  # 4-word sequences said in two different replies


def word_list(text: str) -> list[str]:
    return re.findall(r"[a-zà-ÿ0-9]+", text.lower())


RECAP = re.compile(r"\b(je valide|on valide|je confirme|on confirme|je vous la valide|c'est bon pour vous)\b", re.I)


def repeated_phrases(replies: list[str]) -> list[str]:
    """4-word sequences that come back in a later reply (what makes a call sound repetitive). The recap before
    validation is meant to repeat products, quantities and total: it is left out."""
    seen: dict[tuple[str, ...], int] = {}
    out = []
    for i, reply in enumerate(replies):
        if RECAP.search(reply):
            continue
        w = word_list(reply)
        grams = {tuple(w[j : j + 4]) for j in range(len(w) - 3)}
        for g in grams:
            if g in seen and seen[g] != i and " ".join(g) not in out:
                out.append(" ".join(g))
            seen.setdefault(g, i)
    return out


async def play(s: Scenario, erp: ErpClient, router: bool = True) -> Result:
    r = Result(s.name)
    cid = f"cmp_eval_{s.name}_{int(time.time())}_{random.randint(100, 999)}"
    imported = await erp.post("/api/campaigns/import", {"campaignId": cid, "name": f"Banc : {s.name}", "offers": OFFERS,
                                                        "targets": [{"clientId": CLIENT, "offerIds": [o["offerId"] for o in OFFERS], "score": 1}]})  # fmt: skip
    call_id = imported["calls"][0]["id"]
    try:
        call = await start_call(call_id, "Banc de test")
        history = [ChatMessage(role="user", content=OPENING_CUE)]
        streak, asked = 0, []  # unclear sentences in a row, how the agent asked (like the voice session)
        for line in [None, *s.lines]:
            t0, first, sound, said = time.perf_counter(), None, None, ""
            if line is not None:
                agent_said = next((m.content for m in reversed(history) if m.role == "assistant" and m.content), "")
                route = await classify(line, agent_said or "", call.offers) if router else None
                plan = await plan_turn(call, route, "Banc de test", agent_said or "", None, streak, asked)
                streak = streak + 1 if plan.action == "clarify" else 0
                history.append(ChatMessage(role="user", content=line))
                r.routes.append(f"{route.intent if route else '-'}/{plan.action or '-'}/{plan.llms[0].model}")
            else:
                plan = Plan(fast=False)  # the opening: big model, no routing
            r.turns += 1
            if plan.filler_first:
                sound = int((time.perf_counter() - t0) * 1000)  # the waiting phrase plays before the model starts
            before = sum(1 for m in history if m.role == "assistant")
            async for ev in run_outbound(history, call, plan, "Banc de test"):
                match ev:
                    case TextEvent(delta=d):
                        first = first or int((time.perf_counter() - t0) * 1000)
                        sound = sound or first
                        said += d
                    case ToolCallEvent(call=c):
                        sound = sound or int((time.perf_counter() - t0) * 1000)  # the waiting phrase plays now
                        r.tools.append(c.name)
                    case DoneEvent(usage=u):
                        r.input_tokens += u.input_tokens or 0
                        r.cache_read += u.cache_read_input_tokens or 0
                        r.output_tokens += u.output_tokens or 0
                    case ErrorEvent(message=m):
                        r.problems.append(f"erreur agent : {m}")
            r.steps += sum(1 for m in history if m.role == "assistant") - before  # one assistant message per model call
            r.agent.append(said.strip())
            if line is not None and plan.action == "clarify":
                asked.append(said.strip())
            if first is not None:
                r.first_token_ms.append(first)
            if sound is not None and line is not None:
                r.first_sound_ms.append(sound)
        await end_call(call, history, "Banc de test")  # hang up (records an outcome if the agent did not)

        # ── checks in the ERP ──
        c = await erp.get(f"/api/campaign-calls/{enc(call_id)}")
        if c["status"] != s.expect_status:
            r.problems.append(f"statut {c['status']} au lieu de {s.expect_status}")
        order = await erp.get(f"/api/orders/{enc(c['orderId'])}") if c.get("orderId") else None
        if (order["status"] if order else None) != s.expect_order:
            r.problems.append(f"commande {order['status'] if order else 'aucune'} au lieu de {s.expect_order or 'aucune'}")
        max_pct = {o["offerId"]: o["maxDiscountPct"] for o in OFFERS}
        for line in c.get("accepted") or []:
            if line["discountPct"] > max_pct[line["offerId"]]:
                r.problems.append(f"remise {line['discountPct']} % au-delà du maximum")
            if s.min_discount and line["discountPct"] < s.min_discount:
                r.problems.append(f"pas de geste commercial (remise {line['discountPct']} %)")
        if s.expect_qty:
            got = {line["offerId"]: line["qty"] for line in c.get("accepted") or []}
            if got != s.expect_qty:
                r.problems.append(f"quantités {got} au lieu de {s.expect_qty}")
        # ── how it sounds: all offers in the opening, short replies, no repeated phrases ──
        opening = r.agent[0].lower() if r.agent else ""
        missing = [w for w in OPENING_WORDS if w not in opening]
        if missing:
            r.problems.append(f"ouverture sans : {', '.join(missing)}")
        r.words = [len(word_list(a)) for a in r.agent]
        if r.words and r.words[0] > MAX_WORDS_OPENING:
            r.problems.append(f"ouverture trop longue ({r.words[0]} mots)")
        long = [n for n in r.words[1:] if n > MAX_WORDS]
        if long:
            r.problems.append(f"{len(long)} réponse(s) de plus de {MAX_WORDS} mots ({max(long)} max)")
        r.repeats = repeated_phrases(r.agent)
        if len(r.repeats) > 2:
            r.problems.append(f"répétitions : {' | '.join(r.repeats[:4])}")
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


def median(xs: list[int]) -> int | None:
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else None


async def main() -> None:
    args = sys.argv[1:]
    repeat = int(args[args.index("--repeat") + 1]) if "--repeat" in args else 1
    names = [a for i, a in enumerate(args) if not a.startswith("--") and not (i and args[i - 1] == "--repeat")]
    every, router = "--all" in args, "--no-router" not in args
    chosen = [s for s in SCENARIOS if (s.name in names if names else (every or not s.side_effects))]
    erp = ErpClient("voice_agent", "Banc de test")
    llm, fast = get_llm_client(), get_fast_llm_client()
    print(f"llm={llm.provider}:{llm.model}, rapide={fast.model}, routeur Jev={'oui' if router and get_jev_client() else 'non'}"
          f" — {len(chosen)} scénario(s) × {repeat}\n")  # fmt: skip
    results = []
    for s in chosen:
        for _ in range(repeat):
            try:
                r = await play(s, erp, router)
            except ErpError as e:
                r = Result(s.name, problems=[f"ERP : {e}"])
            results.append(r)
            print(f"{'OK ' if r.ok else 'KO '} {s.name:<20} {r.turns} tours, {r.steps} appels modèle, outils {r.tools}")
            print(f"    1er son {r.first_sound_ms} ms (médiane {median(r.first_sound_ms)}), 1er mot {r.first_token_ms} ms")
            print(f"    routage {r.routes}")
            print(f"    tokens entrée {r.input_tokens} (+ {r.cache_read} lus en cache), sortie {r.output_tokens}")
            print(f"    mots par réponse {r.words}, phrases répétées {len(r.repeats)}")
            for p in r.problems:
                print(f"    ✗ {p}")
            for i, said in enumerate(r.agent):
                print(f"    agent {i + 1} › {said}")
            print()
    passed = sum(r.ok for r in results)
    sounds = [x for r in results for x in r.first_sound_ms]
    slow = sum(x > 1500 for x in sounds)
    print(f"{passed}/{len(results)} réussis — 1er son : médiane {median(sounds)} ms, pire {max(sounds, default=None)} ms, "
          f"{slow}/{len(sounds)} tours au-delà de 1,5 s")  # fmt: skip
    out = Path.cwd() / "eval-outbound.jsonl"
    try:
        with out.open("a", encoding="utf-8") as f:
            for r in results:
                row = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": llm.model, "fast": fast.model, "router": router, **r.__dict__}
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"résultats ajoutés à {out}")
    except OSError as e:
        print(f"(résultats non écrits : {e})")


if __name__ == "__main__":
    asyncio.run(main())
