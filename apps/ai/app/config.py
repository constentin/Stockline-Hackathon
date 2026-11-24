"""
Configuration, read once from the environment at import time.

The repo-root `.env` (see `.env.example`) is loaded if present; real environment variables win.
Every setting has a default, so with no `.env` at all the service runs on the mock providers.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# apps/ai/app/config.py → the repo root is three folders up.
REPO_ROOT = Path(__file__).resolve().parents[3]

load_dotenv(REPO_ROOT / ".env", override=False)  # never overrides variables already set


def env(name: str, default: str = "") -> str:
    """An environment variable, trimmed; empty counts as unset."""
    return os.environ.get(name, "").strip() or default


@dataclass(frozen=True)
class LLMSettings:
    provider: str  # openai | openai-compatible | anthropic | gemini | mock
    model: str  # empty → the provider's default model
    api_key: str
    base_url: str  # empty → the provider's public endpoint
    effort: str  # anthropic only: low | medium | high
    fast_model: str  # outbound calls: the model for the simple turns Jev recognised (empty: the provider small model)


@dataclass(frozen=True)
class STTSettings:
    provider: str  # openai | deepgram | soniox | gradium | mock
    api_key: str
    model: str  # empty → the provider's default model (see clients/factory.py)
    language: str  # ISO code of what the caller speaks
    mock_text: str  # what the mock STT "hears"


@dataclass(frozen=True)
class TTSSettings:
    provider: str  # openai | elevenlabs | gradium | mock
    api_key: str
    model: str  # empty → the provider's default model
    voice: str  # empty → the provider's default (French) voice
    language: str  # elevenlabs language_code; Gradium / OpenAI follow the voice and the text


@dataclass(frozen=True)
class JevSettings:
    api_key: str  # empty → no Jev client (get_jev_client() returns None)
    base_url: str
    model: str


@dataclass(frozen=True)
class TwilioSettings:
    """Phone calls through Twilio (services/phone.py). All four set → the "call my phone" button appears."""

    account_sid: str  # AC… (Twilio console, home page)
    auth_token: str
    from_number: str  # your Twilio number, E.164 (+33…, +1…)
    to_number: str  # the ONLY number the agent ever calls: your own phone, E.164
    public_url: str  # how Twilio reaches this service from the internet (ngrok / cloudflared), https://…
    max_call_s: int  # Twilio hangs up after this (a forgotten call never runs for an hour)

    @property
    def enabled(self) -> bool:
        return bool(self.account_sid and self.auth_token and self.from_number and self.to_number and self.public_url)


@dataclass(frozen=True)
class VoiceTuning:
    """Hands-free turn-taking (services/listener.py, services/turn_taking.py). VOICE_* in .env."""

    silence_ms: int  # silence that ends a normal sentence
    hold_ms: int  # silence tolerated when the sentence is clearly unfinished (« je voudrais… euh »)
    barge_in_words: int  # real words (hesitations not counted) needed to interrupt the agent
    ignore_backchannel: bool  # « oui », « d'accord », « mmh » while the agent talks: it goes on
    reprompt_s: float  # caller silent this long after the agent spoke: a short relaunch (0 = never)
    echo_tail_ms: int  # after the agent's audio stops, its own words heard back still count as echo this long
    addressee_min: float  # Jev: below this probability the caller was talking to someone else (no answer)
    callback_auto: bool  # ring the phone by itself when a callback is due (services/callbacks.py)
    callback_window_min: int  # only callbacks due for less than this are rung by themselves (older: by hand)
    clarity_min: float  # Jev: below this probability the client's sentence is not understood → ask, don't guess
    stt_confidence_min: float  # mean word confidence below this → doubtful transcript (ask if Jev is not sure either)


@dataclass(frozen=True)
class Settings:
    port: int
    erp_api_url: str
    data_dir: Path  # data pack (separate repo); relative paths are resolved from the repo root
    max_steps: int  # max model ↔ tools round-trips per turn
    llm: LLMSettings
    stt: STTSettings
    tts: TTSSettings
    jev: JevSettings  # optional decision model, used next to the LLM (clients/jev.py)
    voice: VoiceTuning
    twilio: TwilioSettings  # optional: real phone calls (services/phone.py)


settings = Settings(
    port=int(env("AI_PORT", "4100")),
    erp_api_url=env("ERP_API_URL", "http://localhost:4000").rstrip("/"),
    data_dir=(REPO_ROOT / env("DATA_DIR", "../hackathon-data")).resolve(),
    max_steps=int(env("AGENT_MAX_STEPS", "8")),
    llm=LLMSettings(
        provider=env("LLM_PROVIDER", "mock").lower(),
        model=env("LLM_MODEL"),
        api_key=env("LLM_API_KEY"),
        base_url=env("LLM_BASE_URL"),
        effort=env("LLM_EFFORT", "low"),
        fast_model=env("LLM_FAST_MODEL"),
    ),
    stt=STTSettings(
        provider=env("STT_PROVIDER", "mock").lower(),
        api_key=env("STT_API_KEY"),
        model=env("STT_MODEL"),
        language=env("STT_LANGUAGE", "fr"),
        mock_text=env("MOCK_STT_TEXT", "Bonjour, il vous reste du bar ?"),
    ),
    tts=TTSSettings(
        provider=env("TTS_PROVIDER", "mock").lower(),
        api_key=env("TTS_API_KEY"),
        model=env("TTS_MODEL"),
        voice=env("TTS_VOICE"),
        language=env("TTS_LANGUAGE", "fr"),
    ),
    voice=VoiceTuning(
        silence_ms=int(env("VOICE_SILENCE_MS", "600")),
        hold_ms=int(env("VOICE_HOLD_MS", "2500")),
        barge_in_words=int(env("VOICE_BARGE_IN_WORDS", "2")),
        ignore_backchannel=env("VOICE_IGNORE_BACKCHANNEL", "1") not in ("0", "false", "no"),
        reprompt_s=float(env("VOICE_REPROMPT_S", "0")),
        echo_tail_ms=int(env("VOICE_ECHO_TAIL_MS", "800")),
        addressee_min=float(env("VOICE_ADDRESSEE_MIN", "0.5")),
        callback_auto=env("VOICE_CALLBACK_AUTO", "1") not in ("0", "false", "no"),
        callback_window_min=int(env("VOICE_CALLBACK_WINDOW_MIN", "15")),
        clarity_min=float(env("VOICE_CLARITY_MIN", "0.4")),
        stt_confidence_min=float(env("VOICE_STT_CONFIDENCE_MIN", "0.6")),
    ),
    jev=JevSettings(
        api_key=env("JEV_API_KEY"),
        base_url=env("JEV_BASE_URL", "https://api.typesafe.ai").rstrip("/"),
        model=env("JEV_MODEL", "jev-latest"),
    ),
    twilio=TwilioSettings(
        account_sid=env("TWILIO_ACCOUNT_SID"),
        auth_token=env("TWILIO_AUTH_TOKEN"),
        from_number=env("TWILIO_FROM_NUMBER").replace(" ", ""),
        to_number=env("TWILIO_TO_NUMBER").replace(" ", ""),
        public_url=env("PUBLIC_URL").rstrip("/"),
        max_call_s=int(env("TWILIO_MAX_CALL_S", "300")),
    ),
)
