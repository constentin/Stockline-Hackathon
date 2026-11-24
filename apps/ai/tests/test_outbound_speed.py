"""Outbound calls, speed & reliability pieces (offline): spoken text, Jev-guarded confirmation, the turn plan,
the model fallback, hands-free end-of-turn detection, the waiting phrases."""

import asyncio
import json

import httpx
import pytest

import app.clients as clients
import app.services.outbound as outbound
from app.clients.base import LLMClient, LLMRequest, LLMText, LLMUsage, STTClient, STTUpdate
from app.schemas import ChatMessage, TextEvent
from app.services import fillers, listener
from app.services.outbound import OutboundCall, Plan, plan_turn, run_outbound, short_name
from app.services.router import Route, same_words
from app.services.speech_text import for_speech
from app.tools import TOOLS
from app.tools.erp import ErpClient
from app.tools.registry import ToolContext

BRIEF = {
    "callId": "ccl_000001",
    "deliveryDate": "2026-11-25",
    "deliveryOn": "mercredi 25 novembre",
    "client": {"name": "Brasserie Lambert", "contact": {"name": "Manon"}},
    "offers": [{"offerId": "off_1", "productName": "Crevette rose cuite 30/40 (colis 5 kg)"}],
}
ORDER = {"id": "ord_1", "number": "CMD-1", "status": "confirmed", "client": {"name": "X"}, "deliveryDate": "2026-11-25",
         "totalHt": 157.3, "lines": [], "warnings": []}  # fmt: skip


def route(intent: str, confidence: float = 0.95, **kw) -> Route:
    return Route(text=kw.pop("text", "oui"), intent=intent, confidence=confidence, offer=kw.pop("offer", None),
                 offer_confidence=kw.pop("offer_confidence", 0.0), complete=kw.pop("complete", 0.9), ms=150)  # fmt: skip


def erp_with(monkeypatch, handler) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(record))
    monkeypatch.setattr(outbound, "ErpClient", lambda actor, name=None: ErpClient(actor, name, base_url="http://erp", http=http))
    return seen


# ── spoken text ──


def test_for_speech():
    assert for_speech("10 kg à 15,73 € HT") == "10 kilos à 15 euros 73 hors taxes"
    assert for_speech("2,5 € le kilo, −20 %") == "2 euros 50 le kilo, moins 20 pour cent"
    assert for_speech("crevettes 30/40, livraison à 15h30") == "crevettes 30 40, livraison à 15 heures 30"
    assert for_speech("commande n° 42 : 12 €") == "commande numéro 42 : 12 euros"
    assert for_speech("Saint-Jacques") == "Saint-Jacques"  # untouched


def test_short_name_and_words():
    assert short_name("Crevette rose cuite 30/40 (colis 5 kg)") == "Crevette rose cuite"
    assert short_name("Noix de Saint-Jacques") == "Noix de Saint-Jacques"
    assert same_words("Oui, c'est bon.", "oui c'est bon")
    call = OutboundCall("ccl_000001", BRIEF)
    assert call.offers == [{"offerId": "off_1", "name": "Crevette rose cuite"}]
    assert "Manon" in call.stt_terms() and "Crevette rose cuite" in call.stt_terms()


# ── confirmation guarded by Jev ──


class FakeJev:
    def __init__(self, p: float):
        self.p = p

    async def yes_no(self, state, question, yes=None, no=None) -> float:
        return self.p


def confirm_ctx(said: str) -> tuple[ToolContext, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"call": {"status": "accepted"}, "order": ORDER})

    http = httpx.AsyncClient(transport=httpx.MockTransport(record))
    messages = [ChatMessage(role="assistant", content="Dix kilos, 157 euros 30. Je valide ?"), ChatMessage(role="user", content=said)]
    return ToolContext(erp=ErpClient("voice_agent", "A", base_url="http://erp", http=http), actor="voice_agent", messages=messages), seen


async def test_confirm_call_order_needs_a_clear_yes(monkeypatch):
    monkeypatch.setattr(clients, "get_jev_client", lambda: FakeJev(0.1))
    ctx, seen = confirm_ctx("Euh attendez, finalement non")
    with pytest.raises(ValueError, match="pas clairement dit oui"):
        await TOOLS["confirm_call_order"].run({"callId": "ccl_000001"}, ctx)
    assert seen == []  # nothing confirmed

    monkeypatch.setattr(clients, "get_jev_client", lambda: FakeJev(0.97))
    ctx, seen = confirm_ctx("Oui c'est bon")
    out = await TOOLS["confirm_call_order"].run({"callId": "ccl_000001"}, ctx)
    assert seen[0].method == "POST" and seen[0].url.path == "/api/campaign-calls/ccl_000001/confirm"
    assert out["callStatus"] == "accepted"


async def test_confirm_call_order_without_jev_trusts_the_model(monkeypatch):
    monkeypatch.setattr(clients, "get_jev_client", lambda: None)
    ctx, seen = confirm_ctx("Oui")
    await TOOLS["confirm_call_order"].run({"callId": "ccl_000001"}, ctx)
    assert len(seen) == 1


# ── turn plan ──


def call_state(**kw):
    return {"id": "ccl_000001", "status": "in_progress", "accepted": [], "orderStatus": None, **kw}


async def test_plan_yes_to_recap_confirms_by_code(monkeypatch):
    state = call_state(accepted=[{"offerId": "off_1"}], orderStatus="draft")
    seen = erp_with(monkeypatch, lambda r: httpx.Response(200, json=state if r.method == "GET" else {"call": {}, "order": ORDER}))
    plan = await plan_turn(OutboundCall("ccl_000001", BRIEF), route("valide"), agent_said="Dix kilos, 157 euros 30. Je valide ?")
    assert plan.fast and plan.action == "confirmed"
    assert [(r.method, r.url.path) for r in seen] == [("GET", "/api/campaign-calls/ccl_000001"), ("POST", "/api/campaign-calls/ccl_000001/confirm")]
    assert "157,3 € HT" in plan.note and "mercredi 25 novembre" in plan.note


async def test_plan_yes_to_another_question_does_not_confirm(monkeypatch):
    state = call_state(accepted=[{"offerId": "off_1"}], orderStatus="draft")
    seen = erp_with(monkeypatch, lambda r: httpx.Response(200, json=state))
    plan = await plan_turn(OutboundCall("ccl_000001", BRIEF), route("valide"), agent_said="Je vous ajoute des bulots ?")
    assert plan.action is None and not plan.fast
    assert all(r.method == "GET" for r in seen)  # nothing confirmed


async def test_plan_refusal_recorded_only_without_accepted_lines(monkeypatch):
    seen = erp_with(monkeypatch, lambda r: httpx.Response(200, json=call_state()))
    plan = await plan_turn(OutboundCall("ccl_000001", BRIEF), route("refuse", text="Non merci"))
    assert plan.action == "declined" and json.loads(seen[1].content)["status"] == "declined"

    erp_with(monkeypatch, lambda r: httpx.Response(200, json=call_state(accepted=[{"offerId": "off_1"}], orderStatus="draft")))
    plan = await plan_turn(OutboundCall("ccl_000001", BRIEF), route("refuse"))
    assert not plan.fast and plan.action is None  # he already took something: the big model handles it


async def test_plan_unsure_or_complex_goes_to_the_big_model(monkeypatch):
    erp_with(monkeypatch, lambda r: httpx.Response(200, json=call_state()))
    call = OutboundCall("ccl_000001", BRIEF)
    assert not (await plan_turn(call, None)).fast
    assert not (await plan_turn(call, route("refuse", confidence=0.65))).fast  # not sure enough to act
    hint = await plan_turn(call, route("negocie", offer="off_1", offer_confidence=0.9))
    assert not hint.fast and "Crevette rose cuite (off_1)" in hint.note


# ── model fallback ──


class Broken(LLMClient):
    provider, model = "test", "broken"

    async def stream(self, req: LLMRequest):
        raise RuntimeError("overloaded")
        yield  # pragma: no cover


class Silent(LLMClient):
    provider, model = "test", "silent"

    async def stream(self, req: LLMRequest):
        await asyncio.sleep(10)
        yield LLMText("trop tard")


class Fine(LLMClient):
    provider, model = "test", "fine"

    async def stream(self, req: LLMRequest):
        yield LLMText("Très bien, bonne journée !")
        yield LLMUsage(0, 0)


async def run_with(monkeypatch, fast: LLMClient, main: LLMClient) -> tuple[str, list[ChatMessage]]:
    monkeypatch.setattr(outbound, "get_fast_llm_client", lambda: fast)
    monkeypatch.setattr(outbound, "get_llm_client", lambda: main)

    async def no_meta():
        return {}

    monkeypatch.setattr("app.agent.prompts.get_meta", no_meta)
    monkeypatch.setattr(outbound, "FIRST_EVENT_TIMEOUT_S", 0.2)
    working = [ChatMessage(role="user", content="Non merci")]
    said = ""
    async for ev in run_outbound(working, OutboundCall("ccl_000001", BRIEF), Plan(fast=True)):
        if isinstance(ev, TextEvent):
            said += ev.delta
    return said, working


async def test_fallback_on_error_and_on_silence(monkeypatch):
    said, working = await run_with(monkeypatch, Broken(), Fine())
    assert said == "Très bien, bonne journée !" and working[-1].content == said and len(working) == 2
    said, _ = await run_with(monkeypatch, Silent(), Fine())
    assert said == "Très bien, bonne journée !"
    said, working = await run_with(monkeypatch, Broken(), Silent())
    assert said == outbound.SORRY and working[-1].content == outbound.SORRY


# ── hands-free end of turn ──


class ScriptedSTT(STTClient):
    """listen() plays a script: (seconds to wait, update) — a finalize on `control` releases the final words."""

    name, listens = "scripted", True

    def __init__(self, script: list[tuple[float, STTUpdate]], final: str):
        self.script, self.final = script, final

    async def transcribe(self, audio, mime, hint=None) -> str:
        return ""

    def finalize_message(self) -> str:
        return "finalize"

    async def listen(self, audio, mime, hint, control):
        for wait, update in self.script:
            await asyncio.sleep(wait)
            yield update
        assert await control.get() == "finalize"
        yield STTUpdate(self.final, final=True, stable=self.final)
        await asyncio.sleep(10)


async def listen_once(monkeypatch, complete: float) -> tuple[str, float]:
    monkeypatch.setattr(listener, "QUIET_S", 0.05)
    monkeypatch.setattr(listener, "SILENCE_S", 0.15)
    monkeypatch.setattr(listener, "HOLD_S", 0.4)
    stt = ScriptedSTT([(0.0, STTUpdate("les moules")), (0.05, STTUpdate("les moules oui"))], "Les moules, oui.")
    got: asyncio.Future = asyncio.get_running_loop().create_future()

    async def on_words(text, stable):
        pass

    async def on_turn(text, last_word_at, r):
        got.set_result((text, r))

    async def judge(text):
        return route("choisit", text=text, complete=complete)

    t0 = asyncio.get_running_loop().time()
    lst = listener.Listener(stt, "audio/webm", None, on_words, on_turn, judge)
    text, r = await asyncio.wait_for(got, 3)
    await lst.close()
    assert r is not None and r.intent == "choisit"  # the Jev answer computed during the silence comes with the turn
    return text, asyncio.get_running_loop().time() - t0


async def test_listener_ends_turn_sooner_when_sentence_is_finished(monkeypatch):
    text, fast = await listen_once(monkeypatch, complete=0.95)
    assert text == "Les moules, oui."
    _, slow = await listen_once(monkeypatch, complete=0.1)  # "je voudrais… euh": waits for the rest
    assert slow > fast + 0.2


# ── waiting phrases ──


def test_filler_never_twice_in_a_row(monkeypatch):
    class T:
        name, model, voice = "fake", "m", "v"

    monkeypatch.setitem(fillers._clips, ("fake", "m", fillers.settings.tts.voice or "v"), [fillers.Clip("A", "audio/mpeg", b"1"), fillers.Clip("B", "audio/mpeg", b"2")])
    assert all(fillers.pick(T(), "A").text == "B" for _ in range(10))
