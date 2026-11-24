"""Hands-free turn-taking (offline): « euh », « oui oui », unfinished sentences, barge-in, a « oui » that answers."""

import asyncio
import time

from app.clients.base import STTClient, STTUpdate
from app.services import listener, turn_taking
from app.services.voice_session import VoiceConnection


def test_word_rules():
    assert turn_taking.only_hesitation("Euh…") and turn_taking.only_hesitation("euh bah hum")
    assert not turn_taking.only_hesitation("euh les moules")
    assert turn_taking.is_backchannel("Oui oui.") and turn_taking.is_backchannel("D'accord.")
    assert turn_taking.is_backchannel("Euh, ok d'accord")
    assert not turn_taking.is_backchannel("Oui mais attendez")
    assert turn_taking.hesitant_end("Je voudrais des moules et") and turn_taking.hesitant_end("Vingt kilos euh")
    assert turn_taking.hesitant_end("C'est pour…") and not turn_taking.hesitant_end("Vingt kilos de moules.")
    assert not turn_taking.is_barge_in("euh bah")  # hesitations are not words
    assert not turn_taking.is_barge_in("oui d'accord")  # listening, not interrupting
    assert turn_taking.is_barge_in("Attendez, non")
    assert turn_taking.asks_question("Quarante kilos, 111 euros 60. Je valide ?")
    assert not turn_taking.asks_question("Les moules se gardent jusqu'à vendredi.")
    opening = "Bonjour Manon, Léa des Parcs de Marennes. Crevettes à 15 euros 73, moules à 2 euros 98. Ça vous intéresse ?"
    assert not turn_taking.during_final_question(opening, 2.0)  # « oui oui » over the offers: listening
    assert turn_taking.during_final_question(opening, 6.5)  # « oui » over the question: the answer


# ── listener: « euh » alone never becomes a turn ──


class Script(STTClient):
    name, listens = "script", True

    def __init__(self, *updates: STTUpdate):
        self.updates = updates

    async def transcribe(self, audio, mime, hint=None) -> str:
        return ""

    def finalize_message(self) -> str:
        return "finalize"

    async def listen(self, audio, mime, hint, control):
        for u in self.updates:
            yield u
        await asyncio.sleep(10)


async def test_hesitation_alone_is_not_a_turn(monkeypatch):
    monkeypatch.setattr(listener, "QUIET_S", 0.02)
    monkeypatch.setattr(listener, "HOLD_S", 0.1)
    turns, dropped = [], []

    async def on_words(text, stable):
        pass

    async def on_turn(text, at, route):
        turns.append(text)

    async def on_dropped(text):
        dropped.append(text)

    async def judge(text):
        raise AssertionError("no Jev call for « euh »")

    lst = listener.Listener(Script(STTUpdate("Euh…", final=True, stable="Euh…")), "audio/webm", None, on_words, on_turn, judge,
                            on_dropped=on_dropped)  # fmt: skip
    await asyncio.sleep(0.3)
    await lst.close()
    assert turns == [] and dropped == ["Euh…"]


# ── the voice session: barge-in and backchannels ──


def connection(agent_said: str, playing: bool) -> tuple[VoiceConnection, list, list]:
    conn = VoiceConnection(ws=None)  # type: ignore[arg-type]
    sent, started = [], []

    async def send(msg):
        sent.append(type(msg).__name__)

    async def interrupt():
        pass

    conn.send, conn.interrupt = send, interrupt  # type: ignore[method-assign]
    conn.start_turn = lambda **kw: started.append(kw["text"])  # type: ignore[method-assign]
    conn.agent_said, conn.agent_playing = agent_said, playing
    return conn, sent, started


async def test_listening_noises_do_not_stop_the_agent():
    conn, sent, started = connection("Je vous explique : les moules sont de Charron, livrées demain.", playing=True)
    for text in ("Euh", "Oui oui", "D'accord", "Hum hum"):
        await conn.on_words(text, "")
        await conn.on_listen_turn(text, time.perf_counter(), None)
    assert started == [] and "CallerSpeakingMessage" not in sent  # the agent went on talking

    await conn.on_words("Attendez, non, pas les moules", "")  # a real interruption
    assert "CallerSpeakingMessage" in sent


async def test_yes_over_a_question_is_the_answer_once_the_agent_has_finished():
    conn, sent, started = connection("Quarante kilos de moules, 111 euros 60. Je valide ?", playing=True)
    conn.playing_since -= 4  # the agent has been speaking for 4 s: it is at its question

    async def finish_speaking():
        await asyncio.sleep(0.15)
        conn.agent_playing = False

    asyncio.create_task(finish_speaking())
    t0 = time.perf_counter()
    await conn.on_listen_turn("Oui.", t0, None)
    assert started == ["Oui."] and time.perf_counter() - t0 >= 0.14  # taken, but not over the agent's last words


def test_echo_tail_after_the_audio_stops():
    conn, _, _ = connection("Les bulots se gardent jusqu'à vendredi.", playing=True)
    conn.agent_playing = False  # just stopped: its last words may still come back through the mic
    assert conn.is_echo("jusqu'à vendredi")
    conn.played_until -= 5
    assert not conn.is_echo("jusqu'à vendredi")


# ── automatic callbacks ──


async def test_due_callbacks_ring_once_and_never_old_ones(monkeypatch):
    from app.services import callbacks, phone

    rung = []

    async def place_call(call_id):
        rung.append(call_id)

    class Erp:
        def __init__(self, *a):
            pass

        async def get(self, path):
            assert path == "/api/campaign-calls/callbacks"
            return {"now": "2026-11-24T15:05", "due": [
                {"id": "ccl_old", "clientName": "A", "callbackAt": "2026-11-24T10:30"},  # 4 h late: by hand only
                {"id": "ccl_new", "clientName": "B", "callbackAt": "2026-11-24T15:00"},
            ], "upcoming": []}  # fmt: skip

    monkeypatch.setattr(phone, "disabled_reason", lambda: None)
    monkeypatch.setattr(phone, "active_call", lambda: None)
    monkeypatch.setattr(phone, "place_call", place_call)
    monkeypatch.setattr(callbacks, "ErpClient", Erp)
    callbacks._tried.clear()
    assert await callbacks.ring_due() == "ccl_new"
    assert await callbacks.ring_due() is None  # tried: not again right away (no answer → the ERP records it)
    assert rung == ["ccl_new"]

    monkeypatch.setattr(phone, "disabled_reason", lambda: "À renseigner dans .env : TWILIO_ACCOUNT_SID")
    callbacks._tried.clear()
    assert await callbacks.ring_due() is None  # no phone: the web banner offers the call instead


# ── openings and callbacks with context ──


def test_openings_change_and_a_callback_picks_the_conversation_up():
    from app.services.outbound import CALLBACK_OPENING, pick_opening

    brief = {"client": {"contact": {"name": "Manon Lefèvre"}}}
    openings = [pick_opening(brief) for _ in range(6)]
    assert all(a != b for a, b in zip(openings, openings[1:]))  # never the same twice in a row
    assert all("Manon" in o for o in openings)
    previous = {**brief, "previousCalls": [{"at": "2026-11-24T15:07", "status": "callback", "lines": ["Client : 20 kilos de moules"]}]}
    assert pick_opening(previous) == CALLBACK_OPENING


def test_transcript_keeps_what_was_said_only():
    from app.schemas import ChatMessage
    from app.services.outbound import OPENING_CUE, transcript

    history = [
        ChatMessage(role="user", content=OPENING_CUE),
        ChatMessage(role="assistant", content="Bonjour Manon !"),
        ChatMessage(role="user", content="[Avant, le client a dit à quelqu'un près de lui : « Kevin ! »]\nVingt kilos de moules.\n[Standard : note]"),
    ]
    assert transcript(history).splitlines() == ["Agent : Bonjour Manon !", "Client : Vingt kilos de moules."]


# ── not understood: ask, don't guess ──


def test_unclear_sentences_get_a_clarifying_question_that_changes():
    from app.services.outbound import OutboundCall, clarify_note, unclear
    from app.services.router import Route

    def r(text, clear, offer=None):
        return Route(text=text, intent="autre", confidence=0.5, offer=offer, offer_confidence=0.9 if offer else 0.0,
                     complete=0.9, ms=150, clear=clear)  # fmt: skip

    assert unclear(r("Le chat des dés vingt pour la cave.", 0.04), None)
    assert not unclear(r("Vingt kilos de moules.", 1.0), None)
    assert unclear(r("Mais vingt kilos.", 0.6), 0.45)  # doubtful transcript and Jev not sure → ask
    assert not unclear(r("Oui.", 0.85), 0.45)  # doubtful transcript but clearly a yes → go on
    assert not unclear(None, 0.2)  # no Jev: the big model decides (its prompt says not to guess)

    call = OutboundCall("ccl_1", {"offers": [{"offerId": "off_2", "productName": "Moule de bouchot de Charron (sac 10 kg)"}]})
    first = clarify_note(r("Mes paiements, 20 kilos.", 0.2, "off_2"), call, 1, [])
    assert "Moule de bouchot de Charron" in first and "le nombre 20" in first and "Ne devine pas" in first
    second = clarify_note(r("Euh le truc là.", 0.1), call, 2, ["Pardon, vous pouvez répéter ?"])
    assert "deuxième fois" in second and "« Pardon, vous pouvez répéter ? »" in second  # another way, never the same words
    assert "rappeler" in clarify_note(r("…", 0.1), call, 3, [])  # third time: the line is bad, offer a callback
