"""
WS /voice — push-to-talk voice sessions (messages: app/schemas/voice.py).

One turn:
    audio_start … binary frames … audio_end   (or a typed `text` message)
      → STT, live: each frame goes to the provider as it arrives (services/utterance.py);
        "transcript" final:false while the caller speaks, then "transcript" final:true soon after audio_end
      → agent (voice mode) streaming "agent_text" / "agent_tool"
      → each finished sentence goes to TTS right away (sentence by sentence = first audio sooner)
      → "agent_audio_start" {mime}, binary audio chunks…, "agent_audio_end"   (one audio segment per turn)
      → "agent_done" {text, timings}

Barge-in: a new audio_start / text while the agent is still busy cancels the running turn (asyncio
cancellation); what was heard and said so far stays in the history.

Outbound clearance call: `start` with campaignCallId → fresh history, the call brief at the end of the system prompt
(services/outbound.py), mode "outbound" with its own tools, "call_started", then a first turn on a bracketed cue so the
AGENT speaks first. `hangup` stops the agent, records the outcome if the agent did not, sends "call_ended".
The brief is not refreshed during the call: changing the system prompt would invalidate the cached history after it
(the campaign tools return fresh quantities anyway).

Outbound turns, for speed and reliability:
  - Jev routing (services/router.py), started on the last partial transcript (push-to-talk: at audio_end, while the
    STT finalizes; hands-free: as soon as the words stop changing), so it is usually ready when the turn starts;
    services/outbound.py plan_turn then picks the model and, when Jev is sure, does the ERP action by code;
  - a pre-recorded waiting phrase (services/fillers.py) the moment the model calls a tool before speaking;
  - the call's own words (products, contact) first in the STT vocabulary;
  - hands-free mode (`listen_start`, services/listener.py): the mic streams the whole call, the server detects the
    end of each sentence and the caller interrupting the agent (`caller_speaking`); the agent's own voice coming back
    through the mic (echo) is ignored.
"""

import asyncio
import json
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import aclosing, suppress
from dataclasses import dataclass, field

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import TypeAdapter, ValidationError

from app.agent import run_agent, sanitize_history
from app.agent.loop import ms_since
from app.clients import STTUpdate, TTSClient, get_llm_client, get_stt_client, get_tts_client
from app.config import settings
from app.schemas import (
    AgentAudioEndMessage,
    AgentAudioStartMessage,
    AgentDoneMessage,
    AgentTextMessage,
    AgentToolMessage,
    AudioEndMessage,
    AudioStartMessage,
    CallEndedMessage,
    CallerSpeakingMessage,
    CallStartedMessage,
    ChatMessage,
    ErrorEvent,
    HangupMessage,
    ListenStartMessage,
    ListenStoppedMessage,
    ListenStopMessage,
    PlaybackMessage,
    ReadyMessage,
    ResetMessage,
    StartMessage,
    TextEvent,
    TextMessage,
    ToolCallEvent,
    ToolResultEvent,
    TranscriptMessage,
    TurnTimings,
    VoiceClientMessage,
    VoiceErrorMessage,
)
from app.schemas.base import WireModel
from app.tools import all_tools
from app.tools.erp import ErpError

from . import fillers
from .listener import Listener
from .outbound import OPENING_CUE, OutboundCall, end_call, plan_turn, run_outbound, start_call
from .router import Route, classify, same_words
from .speech_text import for_speech
from .utterance import Utterance
from .vocabulary import stt_hint

client_message = TypeAdapter(VoiceClientMessage)

ROUTE_WAIT_S = 0.4  # at the start of an outbound turn, wait at most this for Jev (usually already done)
ECHO_SHARE = 0.7  # hands-free: words heard while the agent plays that are mostly ITS words = its own voice (echo)


def words(text: str) -> list[str]:
    return re.findall(r"[a-zà-ÿ0-9']+", text.lower())


@dataclass
class Conversation:
    id: str
    actor_name: str | None = None
    history: list[ChatMessage] = field(default_factory=list)
    call: OutboundCall | None = None  # an outbound clearance call in progress
    last_filler: str | None = None  # the waiting phrase played last (never twice in a row)


# In memory: a reconnect with the same sessionId continues the conversation.
conversations: dict[str, Conversation] = {}


class VoiceConnection:
    """One WebSocket connection. Reads client messages; runs each turn as a background task."""

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.conversation: Conversation | None = None
        self.recording: Utterance | None = None  # while the key is down
        self.turn: asyncio.Task | None = None
        self.warm_up: asyncio.Task | None = None
        self._send_lock = asyncio.Lock()
        self.listener: Listener | None = None  # hands-free mode
        self.caller_open = False  # hands-free: the caller's current sentence is already announced (caller_speaking)
        self.agent_playing = False  # the browser is playing the agent's audio (it says so: `playback`)
        self.agent_said = ""  # what the agent said in the current / last turn (echo detection)
        self.last_partial = ""  # push-to-talk: the last partial transcript (routing starts on it at audio_end)
        self.spec: tuple[str, asyncio.Task] | None = None  # (text, Jev route) started before the final transcript
        self.background: set[asyncio.Task] = set()

    def tts_client(self) -> TTSClient:
        """The TTS of this connection: mp3 for the browser. The phone bridge (services/phone.py) returns µ-law."""
        return get_tts_client()

    # ── sending ──

    async def send(self, msg: WireModel) -> None:
        await self._send(text=msg.to_json())

    async def send_bytes(self, data: bytes) -> None:
        await self._send(bytes=data)

    async def _send(self, **message) -> None:
        async with self._send_lock:
            with suppress(WebSocketDisconnect, RuntimeError):  # the client is gone: nothing to do
                if message.get("text") is not None:
                    await self.ws.send_text(message["text"])
                else:
                    await self.ws.send_bytes(message["bytes"])

    # ── receiving ──

    async def serve(self) -> None:
        await self.ws.accept()
        try:
            while True:
                frame = await self.ws.receive()
                if frame["type"] == "websocket.disconnect":
                    break
                if frame.get("bytes") is not None:
                    if self.listener:
                        self.listener.feed(frame["bytes"])
                    elif self.recording:
                        self.recording.feed(frame["bytes"])
                    continue
                await self.handle_text(frame.get("text") or "")
        except WebSocketDisconnect:
            pass
        finally:
            await self.stop_listening()
            await self.interrupt()

    async def handle_text(self, raw: str) -> None:
        try:
            msg = client_message.validate_python(json.loads(raw))
        except (json.JSONDecodeError, ValidationError):
            await self.send(VoiceErrorMessage(message="invalid JSON message"))
            return
        if not self.conversation and not isinstance(msg, StartMessage):
            await self.open()  # no "start" yet: open a fresh conversation

        match msg:
            case StartMessage(session_id=session_id, actor_name=actor_name, campaign_call_id=call_id):
                await self.interrupt()
                await self.open(session_id, actor_name, fresh=bool(call_id))
                if call_id:
                    await self.dial(call_id)
            case AudioStartMessage(mime=mime, hint=hint):
                await self.interrupt()  # barge-in
                self.last_partial, self.spec = "", None
                self.recording = Utterance(get_stt_client(), mime, self.call_hint(hint), on_partial=self.send_partial)
            case AudioEndMessage():
                if self.recording:
                    utterance, self.recording = self.recording, None
                    if self.conversation.call and self.last_partial:  # route on the last partial while the STT finalizes
                        self.spec = (self.last_partial, asyncio.create_task(self.judge(self.last_partial)))
                    self.start_turn(utterance=utterance)
            case ListenStartMessage(mime=mime):
                await self.start_listening(mime)
            case ListenStopMessage():
                await self.stop_listening()
            case PlaybackMessage(playing=playing):
                self.agent_playing = playing
            case TextMessage(text=text):
                await self.interrupt()
                self.start_turn(text=text)
            case ResetMessage():
                await self.interrupt()
                self.conversation.history = []
                await self.open(self.conversation.id)
            case HangupMessage():
                await self.stop_listening()
                await self.interrupt()
                await self.hang_up()

    async def send_partial(self, update: STTUpdate) -> None:
        self.last_partial = update.text
        await self.send(TranscriptMessage(text=update.text, final=False, stable=update.stable))

    def play_filler(self, conv: "Conversation", tts: TTSClient, speaker: "Speaker", timings: TurnTimings, t0: float) -> str:
        """A pre-recorded waiting phrase, queued now (never the same twice in a row). Returns what it says."""
        clip = fillers.pick(tts, conv.last_filler)
        if not clip or timings.filler_ms is not None:
            return ""
        conv.last_filler = clip.text
        speaker.play(clip)
        timings.filler_ms = ms_since(t0)
        self.agent_said += f"{clip.text} "
        msg = AgentTextMessage(delta=f"{clip.text} ")
        task = asyncio.create_task(self.send(msg))
        self.background.add(task)
        task.add_done_callback(self.background.discard)
        return f"{clip.text} "

    # ── routing (outbound) ──

    def call_hint(self, hint: str | None) -> str | None:
        """During a call, the call's own words (products on offer, contact) go first in the STT vocabulary."""
        call = self.conversation.call if self.conversation else None
        if not call:
            return hint
        return ", ".join(x for x in (call.stt_terms(), hint) if x)

    async def judge(self, text: str) -> Route | None:
        """Jev's reading of the caller's words (intent, product, finished sentence). None outside a call."""
        conv = self.conversation
        if not conv or not conv.call:
            return None
        agent = next((m.content for m in reversed(conv.history) if m.role == "assistant" and m.content), "")
        return await classify(text, agent or "", conv.call.offers)

    async def route_for(self, heard: str) -> tuple[Route | None, int]:
        """The route of this turn: the one started in advance if it was on the same words, else a fresh one —
        waiting at most ROUTE_WAIT_S either way. Returns (route, ms waited)."""
        t0 = time.perf_counter()
        spec, self.spec = self.spec, None
        task = spec[1] if spec and same_words(spec[0], heard) else asyncio.create_task(self.judge(heard))
        try:
            route = await asyncio.wait_for(asyncio.shield(task), ROUTE_WAIT_S)
        except TimeoutError:
            route = None  # too slow this time: the big model takes the turn
        return route, ms_since(t0)

    # ── hands-free ──

    async def start_listening(self, mime: str) -> None:
        await self.stop_listening()
        stt = get_stt_client()
        if not stt.listens:
            await self.send(VoiceErrorMessage(message=f"Mains libres indisponible avec le STT {stt.name} (Soniox requis)"))
            return
        hint = await stt_hint(stt.name, self.call_hint(None))
        self.caller_open = False
        self.listener = Listener(
            stt, mime, hint, on_words=self.on_words, on_turn=self.on_listen_turn, judge=self.judge, on_stopped=self.on_listen_stopped
        )

    async def on_listen_stopped(self, reason: str) -> None:
        self.listener = None  # its stream is over; binary frames go nowhere until a new listen_start
        await self.send(ListenStoppedMessage(reason=f"Écoute mains libres interrompue : {reason}"))

    async def stop_listening(self) -> None:
        listener, self.listener = self.listener, None
        if listener:
            await listener.close()

    def is_echo(self, text: str) -> bool:
        """Words heard while the agent's audio plays, mostly made of what the agent just said: its own voice."""
        if not self.agent_playing:
            return False
        said, heard = set(words(self.agent_said)), words(text)
        return bool(heard) and sum(w in said for w in heard) / len(heard) >= ECHO_SHARE

    async def on_words(self, text: str, stable: str) -> None:
        if not self.caller_open:
            busy = self.agent_playing or (self.turn is not None and not self.turn.done())
            if (busy and len(words(text)) < 2) or self.is_echo(text):
                return  # one word over the agent (or its echo): not a barge-in yet
            self.caller_open = True
            await self.interrupt()  # barge-in: stop the agent's turn
            await self.send(CallerSpeakingMessage())  # the browser stops the audio and opens a caller turn
        await self.send(TranscriptMessage(text=text, final=False, stable=stable))

    async def on_listen_turn(self, text: str, last_word_at: float, route: Route | None) -> None:
        if not self.caller_open:
            if self.is_echo(text):
                return
            await self.interrupt()
            await self.send(CallerSpeakingMessage())
        self.caller_open = False
        if route:
            self.spec = (text, asyncio.create_task(asyncio.sleep(0, route)))  # already known: route_for reuses it
        self.start_turn(text=text, t0=last_word_at)

    async def open(self, session_id: str | None = None, actor_name: str | None = None, fresh: bool = False) -> None:
        """fresh: a new history even if the sessionId is known (each outbound call starts clean)."""
        known = None if fresh else conversations.get(session_id or "")
        conv = known or Conversation(id=session_id or str(uuid.uuid4()))
        if actor_name:
            conv.actor_name = actor_name
        conversations[conv.id] = self.conversation = conv
        llm, stt = get_llm_client(), get_stt_client()
        await self.send(
            ReadyMessage(
                session_id=conv.id, stt=stt.name, tts=get_tts_client().name, llm=f"{llm.provider}:{llm.model}", hands_free=stt.listens
            )
        )
        # Fetch the company vocabulary for the STT now, not when the caller first speaks.
        self.warm_up = self.warm_up or asyncio.create_task(stt_hint(get_stt_client().name, None))

    # ── outbound call ──

    async def dial(self, call_id: str) -> None:
        """Load the call (brief, in_progress), announce it, and let the agent speak first."""
        conv = self.conversation
        warm = asyncio.create_task(fillers.warm(self.tts_client()))  # waiting phrases, once per voice (cached)
        self.background.add(warm)
        warm.add_done_callback(self.background.discard)
        try:
            conv.call = await start_call(call_id, conv.actor_name)
        except ErpError as e:
            await self.send(VoiceErrorMessage(message=f"Appel {call_id} impossible : {e}"))
            return
        def fr(n: float, fmt: str) -> str:
            return format(n, fmt).replace(".", ",")

        units = {"piece": "pièces", "box": "colis", "dozen": "douzaines", "liter": "litres"}

        offers = [
            f"{o['productName']} : {fr(round(o['remaining'], 1), 'g')} {units.get(o['unit'], o['unit'])} à {fr(o['offerPrice'], '.2f')} € (−{fr(o['discountPct'], 'g')} %)"
            for o in conv.call.brief.get("offers", [])
        ]
        await self.send(CallStartedMessage(call_id=call_id, client_name=conv.call.client_name, offers=offers))
        self.start_turn(text=OPENING_CUE, cue=True)

    async def hang_up(self) -> None:
        conv = self.conversation
        if not conv or not conv.call:
            return
        call, conv.call = conv.call, None
        try:
            outcome = await end_call(call, conv.history, conv.actor_name)
        except ErpError as e:
            await self.send(VoiceErrorMessage(message=f"Résultat de l'appel non enregistré : {e}"))
            outcome = None
        await self.send(CallEndedMessage(call_id=call.call_id, outcome=outcome))

    def start_turn(self, **turn_input) -> None:
        self.turn = asyncio.create_task(self.run_turn(self.conversation, **turn_input))

    async def interrupt(self) -> None:
        """Barge-in: drop a recording in progress, stop the running turn and wait until it has cleaned up."""
        if self.recording:
            self.recording.cancel()
            self.recording = None
        if self.turn and not self.turn.done():
            self.turn.cancel()
            with suppress(asyncio.CancelledError):
                await self.turn
        self.turn = None

    # ── one turn ──

    async def run_turn(
        self,
        conv: Conversation,
        text: str | None = None,
        utterance: Utterance | None = None,  # spoken: already being transcribed since audio_start
        cue: bool = False,  # a bracketed cue from the "switchboard" (outbound opening): not shown as caller speech
        t0: float | None = None,  # hands-free: when the last word was heard (default: now = the key release)
    ) -> None:
        timings = TurnTimings()
        if t0 is not None:
            timings.endpoint_ms = ms_since(t0)
        t0 = t0 or time.perf_counter()  # end of caller speech
        heard, spoken = "", ""
        step_text = ""  # what the model said since the last tool result (not yet in `working`)
        working: list[ChatMessage] | None = None
        committed = False
        speaker: Speaker | None = None
        try:
            # 1. Speech → text: with a streaming STT only the last words are left to finalize
            if utterance is not None:
                text = await utterance.text()
                timings.stt_ms = ms_since(t0)  # end of speech → final transcript
            heard = (text or "").strip()
            if not heard:
                await self.send(VoiceErrorMessage(message="Rien entendu — réessayez."))
                return
            if not cue:
                await self.send(TranscriptMessage(text=heard, final=True))

            # 2. Agent, on a working copy of the history (committed at the end)
            working = [*conv.history, ChatMessage(role="user", content=heard)]
            tts = self.tts_client()
            speaker = Speaker(tts, self.send, self.send_bytes)
            self.agent_said = ""
            call = conv.call
            if call:  # outbound: Jev route → plan (model, ERP action done by code) → agent with fallback
                route, timings.route_ms = (None, None) if cue else await self.route_for(heard)
                agent_said = next((m.content for m in reversed(conv.history) if m.role == "assistant" and m.content), "")
                plan = await plan_turn(call, route, conv.actor_name, agent_said or "")
                timings.intent, timings.action = (route.intent if route else None), plan.action
                timings.model = plan.llms[0].model
                events = run_outbound(working, call, plan, conv.actor_name)
                if plan.filler_first:
                    spoken += self.play_filler(conv, tts, speaker, timings, t0)
            else:
                llm = get_llm_client()
                timings.model = llm.model
                events = run_agent(working, llm=llm, tools=all_tools(), mode="voice", actor_name=conv.actor_name)
            llm_start = time.perf_counter()
            async for ev in events:
                match ev:
                    case TextEvent(delta=delta):
                        if timings.llm_first_token_ms is None:
                            timings.llm_first_token_ms = ms_since(llm_start)
                        spoken += delta
                        step_text += delta
                        self.agent_said += delta
                        await self.send(AgentTextMessage(delta=delta))
                        speaker.push(delta)
                    case ToolCallEvent(call=tool_call):
                        if call and not spoken.strip() and timings.filler_ms is None:
                            # the model works before speaking: a pre-recorded waiting phrase, right now
                            spoken += self.play_filler(conv, tts, speaker, timings, t0)
                        await self.send(AgentToolMessage(name=tool_call.name, args=tool_call.arguments))
                    case ToolResultEvent(name=name, ok=ok, ms=ms):
                        step_text = ""
                        await self.send(AgentToolMessage(name=name, ok=ok, ms=ms))
                    case ErrorEvent(message=message):
                        await self.send(VoiceErrorMessage(message=message))
            timings.llm_total_ms = ms_since(llm_start)
            conv.history = trim(working)
            committed = True

            # 3. Speak the last sentence and wait until all the audio is sent
            await speaker.finish()
            timings.tts_first_byte_ms = speaker.tts_first_byte_ms
            if speaker.first_byte_at:
                timings.total_ms = int((speaker.first_byte_at - t0) * 1000 + 0.5)
            await self.send(AgentDoneMessage(text=spoken.strip(), timings=timings))

        except asyncio.CancelledError:
            # Interrupted: keep what was heard, the tool rounds already done (an order the agent created must stay
            # known) and what the agent had started to say. Not when the turn was already committed (barge-in during
            # the last sentence's audio): that would duplicate it. trim() closes a tool call cut mid-run.
            if heard and not committed:
                kept = working if working is not None else [*conv.history, ChatMessage(role="user", content=heard)]
                last = kept[-1]
                if step_text.strip() and not (last.role == "assistant" and last.tool_calls):
                    kept = [*kept, ChatMessage(role="assistant", content=f"{step_text.strip()} …")]
                conv.history = trim(kept)
            if speaker:
                await speaker.stop()
            raise
        except Exception as e:
            await self.send(VoiceErrorMessage(message=str(e) or type(e).__name__))
        finally:
            if utterance:
                utterance.cancel()  # interrupted while transcribing: close the provider stream


class Speaker:
    """Buffers the streamed text, cuts it into sentences and speaks them in order, as one audio segment."""

    # a sentence ends with . ! ? … followed by a space ("18.50" is not a boundary)
    SENTENCE = re.compile(r"^([\s\S]*?[.!?…])\s+")

    def __init__(self, tts: TTSClient, send: Callable[[WireModel], Awaitable], send_bytes: Callable[[bytes], Awaitable]):
        self.tts, self.send, self.send_bytes = tts, send, send_bytes
        self.buffer = ""
        self.sentences: asyncio.Queue[str | fillers.Clip | None] = asyncio.Queue()  # text to synthesize, or ready audio
        self.worker: asyncio.Task | None = None  # speaks the queued sentences one after the other
        self.started = False
        self.first_byte_at: float | None = None
        self.tts_first_byte_ms: int | None = None

    def push(self, delta: str) -> None:
        self.buffer += delta
        while m := self.SENTENCE.match(self.buffer):
            self.speak(m[1])
            self.buffer = self.buffer[m.end() :]

    def speak(self, sentence: str) -> None:
        if not sentence.strip():
            return
        self._queue(sentence)

    def play(self, clip: fillers.Clip) -> None:
        """Pre-recorded audio (a waiting phrase), queued like a sentence: no TTS call."""
        self._queue(clip)

    def _queue(self, item: "str | fillers.Clip") -> None:
        if self.worker is None:
            self.worker = asyncio.create_task(self._speak_queued())
        self.sentences.put_nowait(item)

    async def finish(self) -> None:
        """Speak what is left in the buffer, wait for all the audio, close the segment."""
        self.speak(self.buffer)
        self.buffer = ""
        if self.worker:
            self.sentences.put_nowait(None)
            await self.worker
        if self.started:
            await self.send(AgentAudioEndMessage())

    async def stop(self) -> None:
        """Interrupted: drop the queued sentences, close the segment if audio was sent."""
        if self.worker:
            self.worker.cancel()
            with suppress(asyncio.CancelledError):
                await self.worker
        if self.started:
            await self.send(AgentAudioEndMessage())

    async def _speak_queued(self) -> None:
        while (sentence := await self.sentences.get()) is not None:
            tts_start = time.perf_counter()
            if isinstance(sentence, fillers.Clip):
                if not self.started:
                    self.started = True
                    await self.send(AgentAudioStartMessage(mime=sentence.mime))
                    self.first_byte_at = time.perf_counter()
                    self.tts_first_byte_ms = 0
                await self.send_bytes(sentence.audio)
                continue
            try:
                # spoken form: "15,73 €" → "15 euros 73", "kg" → "kilos"… (services/speech_text.py)
                result = await self.tts.synthesize(for_speech(sentence), settings.tts.voice or None)
                # aclosing: on barge-in (cancellation) the provider's HTTP stream is closed right away
                async with aclosing(result.chunks) as chunks:  # type: ignore[type-var]
                    async for chunk in chunks:
                        if not self.started:
                            self.started = True
                            await self.send(AgentAudioStartMessage(mime=result.mime))
                            self.first_byte_at = time.perf_counter()
                            self.tts_first_byte_ms = ms_since(tts_start)
                        await self.send_bytes(chunk)
            except Exception as e:
                await self.send(VoiceErrorMessage(message=f"TTS : {e}"))


def trim(history: list[ChatMessage], max_messages: int = 40) -> list[ChatMessage]:
    """Keep the last ~40 messages, starting on a user message (never split a tool call from its result)."""
    history = sanitize_history(history)  # no dangling tool call (cancelled mid-tool), no orphan result
    if len(history) <= max_messages:
        return history
    start = next((i for i, m in enumerate(history) if i >= len(history) - max_messages and m.role == "user"), -1)
    return history[start:] if start > 0 else history

