"""
WS /voice contract: push-to-talk sessions, two tracks (the human "caller" and the agent).

Client → server: JSON messages below, plus binary audio frames between audio_start and audio_end.
Server → client: JSON messages below, plus binary audio frames between agent_audio_start and agent_audio_end.
"""

from typing import Annotated, Any, Literal, Union

from pydantic import Field

from .base import WireModel
from .chat import TurnTimings

# ── Client → server ──


class StartMessage(WireModel):
    """Opens a conversation (same sessionId again = keep the history)."""

    type: Literal["start"] = "start"
    session_id: str | None = None
    actor_name: str | None = None
    # Outbound clearance call (ccl_…): a fresh conversation where the AGENT speaks first (call_started, then its turn).
    campaign_call_id: str | None = None


class AudioStartMessage(WireModel):
    """Caller pressed the talk key; binary frames follow."""

    type: Literal["audio_start"] = "audio_start"
    mime: str
    hint: str | None = None  # optional STT hint (vocabulary; mock STT: the transcript), like x-mock-text on POST /command


class AudioEndMessage(WireModel):
    """Caller released the key: the frames received since audio_start are the utterance (already streamed to the
    STT as they arrived: send them every 100–250 ms, e.g. MediaRecorder.start(100))."""

    type: Literal["audio_end"] = "audio_end"


class TextMessage(WireModel):
    """Typed instead of spoken (debug)."""

    type: Literal["text"] = "text"
    text: str


class ResetMessage(WireModel):
    type: Literal["reset"] = "reset"


class HangupMessage(WireModel):
    """Ends the outbound call in progress: the agent stops, the outcome is recorded (if the agent did not), call_ended."""

    type: Literal["hangup"] = "hangup"


class ListenStartMessage(WireModel):
    """Hands-free mode on: binary frames now stream continuously; the server decides when the caller has finished
    (services/listener.py) and when he interrupts the agent. Only when `ready.handsFree` is true."""

    type: Literal["listen_start"] = "listen_start"
    mime: str = "audio/webm"


class ListenStopMessage(WireModel):
    type: Literal["listen_stop"] = "listen_stop"


class PlaybackMessage(WireModel):
    """The browser starts / stops playing the agent's audio (hands-free: echo and barge-in decisions)."""

    type: Literal["playback"] = "playback"
    playing: bool
    played_ms: int | None = None  # when it stops because the caller cut it: how far it had played (what he heard)


VoiceClientMessage = Annotated[
    Union[
        StartMessage, AudioStartMessage, AudioEndMessage, TextMessage, ResetMessage, HangupMessage,
        ListenStartMessage, ListenStopMessage, PlaybackMessage,
    ],
    Field(discriminator="type"),
]

# ── Server → client ──


class ReadyMessage(WireModel):
    type: Literal["ready"] = "ready"
    session_id: str
    stt: str
    tts: str
    llm: str
    hands_free: bool = False  # the STT supports listen_start (continuous listening)


class TranscriptMessage(WireModel):
    """What the caller said. While they speak (streaming STT): final=false, the whole text heard so far, sent again
    as words are recognised; `stable` is its leading part made of final words (will not change, the rest may).
    Then once final=true with the definitive text, which the agent answers."""

    type: Literal["transcript"] = "transcript"
    track: Literal["caller"] = "caller"
    text: str
    final: bool
    stable: str | None = None


class CallerSpeakingMessage(WireModel):
    """Hands-free: the caller started a new sentence (the server stopped the agent if it was talking).
    Partial and final `transcript` messages follow, as in push-to-talk."""

    type: Literal["caller_speaking"] = "caller_speaking"


class AgentCutMessage(WireModel):
    """The caller cut the agent: what he did NOT hear of its reply (the agent knows it, and says what matters again)."""

    type: Literal["agent_cut"] = "agent_cut"
    unheard: str


class ListenStoppedMessage(WireModel):
    """Hands-free listening stopped by the server (the STT stream failed): the page may start it again (a new
    recording = a new stream) or fall back to push-to-talk."""

    type: Literal["listen_stopped"] = "listen_stopped"
    reason: str


class AgentTextMessage(WireModel):
    type: Literal["agent_text"] = "agent_text"
    delta: str


class AgentToolMessage(WireModel):
    """Sent twice per tool: once with `args` when called, once with `ok`/`ms` when done."""

    type: Literal["agent_tool"] = "agent_tool"
    name: str
    ok: bool | None = None
    ms: int | None = None
    args: dict[str, Any] | None = None


class AgentAudioStartMessage(WireModel):
    """Binary audio frames follow (one segment per turn)."""

    type: Literal["agent_audio_start"] = "agent_audio_start"
    mime: str


class AgentAudioEndMessage(WireModel):
    type: Literal["agent_audio_end"] = "agent_audio_end"


class AgentDoneMessage(WireModel):
    type: Literal["agent_done"] = "agent_done"
    text: str
    timings: TurnTimings


class VoiceErrorMessage(WireModel):
    type: Literal["error"] = "error"
    message: str


class CallStartedMessage(WireModel):
    """Outbound call opened (after `ready`): who is called and what is offered. The agent's first turn follows."""

    type: Literal["call_started"] = "call_started"
    call_id: str
    client_name: str
    offers: list[str]  # "Moule de bouchot de Charron : 120 kg à 2,98 €"


class CallEndedMessage(WireModel):
    """Outbound call closed after `hangup`. outcome = what was recorded at hang-up; absent when the agent had
    already recorded it (record_call_outcome)."""

    type: Literal["call_ended"] = "call_ended"
    call_id: str
    outcome: str | None = None


VoiceServerMessage = Annotated[
    Union[
        ReadyMessage,
        TranscriptMessage,
        AgentTextMessage,
        AgentToolMessage,
        AgentAudioStartMessage,
        AgentAudioEndMessage,
        AgentDoneMessage,
        VoiceErrorMessage,
        CallStartedMessage,
        CallEndedMessage,
        CallerSpeakingMessage,
        ListenStoppedMessage,
        AgentCutMessage,
    ],
    Field(discriminator="type"),
]
