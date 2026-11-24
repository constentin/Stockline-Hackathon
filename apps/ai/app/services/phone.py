"""
Real phone calls through Twilio: the agent rings YOUR phone (TWILIO_TO_NUMBER) for an outbound clearance call.

    POST /phone/call {campaignCallId}
      → Twilio REST "create call" with inline TwiML (no webhook to host):
            <Connect><Stream url="wss://PUBLIC_URL/phone/media"><Parameter name="token" value="…"/></Stream></Connect>
    you pick up
      → Twilio opens WS /phone/media (Media Streams) and sends JSON events:
            connected · start {streamSid, callSid, customParameters} · media {payload: base64 µ-law 8 kHz, 20 ms}
            · mark {name} (an audio we sent has finished playing) · stop (hang-up)
    PhoneConnection = the browser's VoiceConnection (services/voice_session.py) in hands-free mode, with the audio
    going to and from Twilio instead of the browser. Everything else is the same code: Soniox listening, end of turn
    (listener.py), Jev routing, the outbound agent, waiting phrases, barge-in, outcome recorded at hang-up.
        caller audio → listener.feed()                (Soniox told it is raw µ-law: clients/soniox.py)
        agent audio  → {"event": "media"}             (ElevenLabs ulaw_8000: no conversion; Gradium: PCM → µ-law)
                       then {"event": "mark"}         Twilio echoes it once played = "the agent stopped talking"
        barge-in     → {"event": "clear"}             Twilio drops the agent audio not played yet
    The agent hangs up by itself once the call is closed in the ERP (order confirmed, refusal…) and its last sentence
    has been heard.

Live screen: every message the browser would get (transcripts, agent text, tool calls, timings, call_started /
call_ended) is broadcast to the web pages watching WS /voice/phone, so the Voix page shows the phone call live.

Safety: only TWILIO_TO_NUMBER is ever called (never the fictional numbers of the ERP); the media WS only accepts a
call this service placed (random token); Twilio hangs up after TWILIO_MAX_CALL_S whatever happens.
"""

import asyncio
import base64
import json
import secrets
import time
from contextlib import suppress
from dataclasses import dataclass, field
from xml.sax.saxutils import quoteattr

from fastapi import WebSocket, WebSocketDisconnect

from app.clients import PHONE_MIME, ConfigError, TTSClient, get_phone_tts_client, get_stt_client
from app.clients.http import http_client
from app.config import settings
from app.schemas import AgentAudioEndMessage, AgentAudioStartMessage, AgentDoneMessage, CallEndedMessage, CallStartedMessage, ReadyMessage
from app.schemas.base import WireModel
from app.schemas.voice import CallerSpeakingMessage
from app.tools import ErpClient
from app.tools.erp import ErpError, enc

from .voice_session import VoiceConnection

ACTOR_NAME = "Agent vocal (téléphone)"
TWILIO_API = "https://api.twilio.com/2010-04-01"
RING_S = 25  # Twilio gives up ringing after this (→ status "no-answer")
HANGUP_GRACE_S = 0.8  # after the agent's last sentence has been heard, before hanging up
MEDIA_CHUNK = 8000  # bytes of µ-law per Twilio media message (1 s of audio)
NOT_ANSWERED = ("busy", "no-answer", "failed", "canceled")


class PhoneError(Exception):
    """Twilio refused, or the phone feature is not configured: the message is shown to the user."""


# ───────── Setup check ─────────


def mask(number: str) -> str:
    """+33612345678 → +336 •• •• •• 78: enough to recognise your own phone, not to read it out."""
    return f"{number[:4]} •• •• •• {number[-2:]}" if len(number) > 6 else "••"


def disabled_reason() -> str | None:
    """None when the agent can call a phone, else why not (in French, shown in the UI)."""
    cfg = settings.twilio
    missing = [
        name
        for name, value in (
            ("TWILIO_ACCOUNT_SID", cfg.account_sid),
            ("TWILIO_AUTH_TOKEN", cfg.auth_token),
            ("TWILIO_FROM_NUMBER", cfg.from_number),
            ("TWILIO_TO_NUMBER", cfg.to_number),
            ("PUBLIC_URL", cfg.public_url),
        )
        if not value
    ]
    if missing:
        return f"À renseigner dans .env : {', '.join(missing)}"
    if not cfg.public_url.startswith("https://"):
        return "PUBLIC_URL doit être l'adresse https:// du tunnel (ngrok, cloudflared)"
    try:
        get_phone_tts_client()
    except ConfigError as e:
        return str(e)
    if not get_stt_client().listens:
        return "Le téléphone a besoin d'un STT mains libres (STT_PROVIDER=soniox)"
    return None


# ───────── Calls placed by this service ─────────


@dataclass
class PhoneCall:
    campaign_call_id: str
    token: str
    call_sid: str = ""
    answered: bool = False  # the media stream started (you picked up)
    created_at: float = field(default_factory=time.monotonic)


calls: dict[str, PhoneCall] = {}  # token → call, from the dial until Twilio's final status


def active_call() -> PhoneCall | None:
    """The phone call in progress, if any (stale entries — Twilio never reported — expire after the time limit)."""
    limit = settings.twilio.max_call_s + RING_S + 60
    for token, call in list(calls.items()):
        if time.monotonic() - call.created_at > limit:
            calls.pop(token, None)
    return next(iter(calls.values()), None)


def stream_url() -> str:
    """https://abc.ngrok-free.app → wss://abc.ngrok-free.app/phone/media (Twilio needs a secure WebSocket)."""
    base = settings.twilio.public_url
    return "wss://" + base.split("://", 1)[-1] + "/phone/media"


def twiml(token: str) -> str:
    """What Twilio does once you pick up: stream the call, both ways, to /phone/media. Twilio's Stream URL takes no
    query string: the token travels as a custom parameter (the start event's customParameters)."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?><Response><Connect>'
        f'<Stream url={quoteattr(stream_url())}><Parameter name="token" value={quoteattr(token)}/></Stream>'
        "</Connect></Response>"
    )


async def twilio_post(path: str, data: dict[str, str]) -> dict:
    cfg = settings.twilio
    res = await http_client().post(
        f"{TWILIO_API}/Accounts/{cfg.account_sid}/{path}", data=data, auth=(cfg.account_sid, cfg.auth_token), timeout=15
    )
    if not res.is_success:
        try:
            message = res.json().get("message") or res.text
        except ValueError:
            message = res.text
        raise PhoneError(f"Twilio {res.status_code} : {message[:300]}")
    return res.json()


async def place_call(campaign_call_id: str) -> PhoneCall:
    """Ring TWILIO_TO_NUMBER. The call itself starts in PhoneConnection when you pick up."""
    if reason := disabled_reason():
        raise PhoneError(reason)
    if active_call():
        raise PhoneError("Un appel téléphonique est déjà en cours : raccrochez-le d'abord")
    cfg = settings.twilio
    token = secrets.token_urlsafe(18)
    call = calls[token] = PhoneCall(campaign_call_id, token)
    try:
        res = await twilio_post(
            "Calls.json",
            {
                "To": cfg.to_number,
                "From": cfg.from_number,
                "Twiml": twiml(token),
                "StatusCallback": f"{cfg.public_url}/phone/status?token={token}",
                "StatusCallbackMethod": "POST",
                "Timeout": str(RING_S),
                "TimeLimit": str(cfg.max_call_s),
            },
        )
    except Exception:
        calls.pop(token, None)
        raise
    call.call_sid = res.get("sid", "")
    print(f"[phone] appel {call.call_sid} vers {mask(cfg.to_number)} (campagne {campaign_call_id})", flush=True)
    return call


async def end_phone_call(call: PhoneCall) -> None:
    """Hang up through Twilio (ringing or in progress). Twilio then sends `stop` on the media stream."""
    if not call.call_sid:
        return
    status = "completed" if call.answered else "canceled"
    try:
        await twilio_post(f"Calls/{call.call_sid}.json", {"Status": status})
    except PhoneError as e:
        if status == "canceled":  # it was answered meanwhile: hang it up instead
            await twilio_post(f"Calls/{call.call_sid}.json", {"Status": "completed"})
        else:
            raise e


async def on_status(token: str, status: str) -> None:
    """Twilio's final status callback. Not answered (busy, no answer, refused…) → recorded as no_answer."""
    call = calls.get(token)
    if not call:
        return
    print(f"[phone] statut Twilio : {status}", flush=True)
    if status not in (*NOT_ANSWERED, "completed"):
        return
    calls.pop(token, None)
    if status in NOT_ANSWERED and not call.answered:
        erp = ErpClient("voice_agent", ACTOR_NAME)
        with suppress(ErpError):
            await erp.patch(
                f"/api/campaign-calls/{enc(call.campaign_call_id)}", {"status": "no_answer", "summary": f"Pas de réponse au téléphone ({status})"}
            )
        broadcast(CallEndedMessage(call_id=call.campaign_call_id, outcome="no_answer"))


# ───────── Web pages watching the call (WS /voice/phone) ─────────

watchers: set[asyncio.Queue[str]] = set()
live_call: CallStartedMessage | None = None  # replayed to a page opened (or reloaded) during the call


def watch() -> asyncio.Queue[str]:
    q: asyncio.Queue[str] = asyncio.Queue(maxsize=2000)
    if live_call:
        q.put_nowait(live_call.to_json())
    watchers.add(q)
    return q


def unwatch(q: asyncio.Queue[str]) -> None:
    watchers.discard(q)


def broadcast(msg: WireModel) -> None:
    global live_call
    if isinstance(msg, CallStartedMessage):
        live_call = msg
    elif isinstance(msg, CallEndedMessage):
        live_call = None
    raw = msg.to_json()
    for q in list(watchers):
        with suppress(asyncio.QueueFull):  # a page that stopped reading: it misses messages, the call goes on
            q.put_nowait(raw)


# ───────── The bridge: Twilio media stream ↔ voice session ─────────


class PhoneConnection(VoiceConnection):
    """One Twilio media stream = one phone call. See the module docstring."""

    def __init__(self, ws: WebSocket):
        super().__init__(ws)
        self.phone: PhoneCall | None = None
        self.stream_sid = ""
        self.marks = 0
        self.unplayed: set[str] = set()  # marks sent and not echoed yet = agent audio still playing on the phone
        self.turn_seq = 0
        self.closing = False  # the call is recorded in the ERP: hang up once the agent's last words are heard
        self.hanging_up: asyncio.Task | None = None
        self.listen_restarts = 0

    def tts_client(self) -> TTSClient:
        return get_phone_tts_client()

    # ── to Twilio ──

    async def twilio(self, event: dict) -> None:
        await self._send(text=json.dumps({**event, "streamSid": self.stream_sid}))

    async def send_bytes(self, data: bytes) -> None:
        for i in range(0, len(data), MEDIA_CHUNK):
            await self.twilio({"event": "media", "media": {"payload": base64.b64encode(data[i : i + MEDIA_CHUNK]).decode()}})

    async def send(self, msg: WireModel) -> None:
        """The voice session's messages: audio framing becomes Twilio marks / clear, the rest goes to the screen."""
        match msg:
            case ReadyMessage():
                return
            case AgentAudioStartMessage():
                self.agent_playing = True
                return
            case AgentAudioEndMessage():
                self.marks += 1
                name = f"m{self.marks}"
                self.unplayed.add(name)
                await self.twilio({"event": "mark", "mark": {"name": name}})
                return
            case CallerSpeakingMessage():  # barge-in: the caller talks over the agent
                await self.twilio({"event": "clear"})
                self.unplayed.clear()
                self.agent_playing = False
                self.cancel_hang_up()
            case AgentDoneMessage():
                self.spawn(self.check_finished(self.turn_seq))
        broadcast(msg)

    # ── from Twilio ──

    async def serve(self) -> None:
        await self.ws.accept()
        try:
            while True:
                event = json.loads(await self.ws.receive_text())
                match event.get("event"):
                    case "start":
                        if not await self.on_start(event.get("start") or {}):
                            break
                    case "media":
                        if self.listener:
                            self.listener.feed(base64.b64decode(event["media"]["payload"]))
                    case "mark":
                        self.on_mark((event.get("mark") or {}).get("name", ""))
                    case "stop":
                        break
        except (WebSocketDisconnect, RuntimeError, json.JSONDecodeError, KeyError):
            pass
        finally:
            if self.hanging_up and self.hanging_up is not asyncio.current_task():
                self.hanging_up.cancel()
            await self.stop_listening()
            await self.interrupt()
            await self.hang_up()  # records the outcome if the agent did not, sends call_ended (→ the screen)
            for task in list(self.background):
                task.cancel()
            if self.phone:
                calls.pop(self.phone.token, None)
                print(f"[phone] appel {self.phone.call_sid} terminé", flush=True)

    async def on_start(self, start: dict) -> bool:
        """You picked up: open a fresh conversation on the campaign call, listen, let the agent speak first."""
        token = (start.get("customParameters") or {}).get("token", "")
        phone = calls.get(token)
        if not phone:
            print("[phone] flux média refusé : appel inconnu (jeton absent ou expiré)", flush=True)
            return False
        phone.answered = True
        self.phone, self.stream_sid = phone, start.get("streamSid", "")
        await self.open(f"phone-{start.get('callSid', token)}", ACTOR_NAME, fresh=True)
        await self.dial(phone.campaign_call_id)  # loads the call and starts the agent's opening sentence (a task)
        if not self.conversation or not self.conversation.call:
            return False  # the ERP refused the call (error already on screen): hang up
        await self.start_listening(PHONE_MIME)  # after dial: the call's products and contact go in the STT vocabulary
        return self.listener is not None

    def on_mark(self, name: str) -> None:
        self.unplayed.discard(name)
        if not self.unplayed:
            self.agent_playing = False
            if self.closing:
                self.hang_up_soon()

    async def on_listen_stopped(self, reason: str) -> None:
        """The STT stream failed mid-call: tell the screen, then open a new one (twice at most)."""
        await super().on_listen_stopped(reason)
        if self.stream_sid and self.listen_restarts < 2:
            self.listen_restarts += 1
            await self.start_listening(PHONE_MIME)

    # ── the agent hangs up by itself ──

    def start_turn(self, **turn_input) -> None:
        self.turn_seq += 1
        super().start_turn(**turn_input)

    async def check_finished(self, seq: int) -> None:
        """After each agent turn: if the call is now closed in the ERP, hang up once the last words are heard."""
        conv = self.conversation
        if not conv or not conv.call:
            return
        try:
            state = await ErpClient("voice_agent", ACTOR_NAME).get(f"/api/campaign-calls/{enc(conv.call.call_id)}")
        except ErpError:
            return
        if state.get("status") in ("pending", "in_progress") or seq != self.turn_seq:
            return  # still open, or the caller already started a new sentence
        self.closing = True
        if not self.unplayed:
            self.hang_up_soon()

    def hang_up_soon(self) -> None:
        if not self.hanging_up or self.hanging_up.done():
            self.hanging_up = asyncio.create_task(self._hang_up_after_grace())

    def cancel_hang_up(self) -> None:
        self.closing = False
        if self.hanging_up and not self.hanging_up.done():
            self.hanging_up.cancel()

    async def _hang_up_after_grace(self) -> None:
        await asyncio.sleep(HANGUP_GRACE_S)
        print("[phone] l'agent raccroche (appel enregistré dans l'ERP)", flush=True)
        try:
            if self.phone:
                await end_phone_call(self.phone)
                return
        except Exception as e:
            print(f"[phone] raccrochage Twilio impossible ({e}) : fermeture du flux", flush=True)
        with suppress(Exception):
            await self.ws.close()  # ending the stream ends the <Connect>, so the call

    def spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self.background.add(task)
        task.add_done_callback(self.background.discard)
