"""Phone calls through Twilio (services/phone.py): audio formats, TwiML, and the Twilio ↔ voice session translation.
No network: Twilio, the ERP and the providers are not called."""

import asyncio
import base64
import json

from app.clients.audio import PcmToMulaw
from app.clients.elevenlabs import ElevenLabsTTS
from app.clients.soniox import SonioxSTT
from app.schemas import AgentAudioEndMessage, AgentAudioStartMessage, CallEndedMessage, TranscriptMessage
from app.schemas.voice import CallerSpeakingMessage
from app.services import phone


class FakeTwilioWS:
    """What PhoneConnection writes to Twilio."""

    def __init__(self):
        self.sent: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))


def test_soniox_is_told_the_phone_audio_is_raw_mulaw():
    stt = SonioxSTT("key")
    req = stt.start_request(None, "audio/x-mulaw")
    assert (req["audio_format"], req["sample_rate"], req["num_channels"]) == ("mulaw", 8000, 1)
    assert stt.start_request(None, "audio/webm;codecs=opus")["audio_format"] == "auto"
    assert "sample_rate" not in stt.start_request(None)


def test_elevenlabs_phone_variant_asks_for_ulaw():
    assert ElevenLabsTTS("k", phone=True).phone and not ElevenLabsTTS("k").phone


def test_pcm_48k_becomes_mulaw_8k_across_odd_chunks():
    enc = PcmToMulaw(48000)
    pcm = b"\x00\x10" * 4800  # 0.1 s at 48 kHz, 16-bit
    out = enc.encode(pcm[:4801]) + enc.encode(pcm[4801:])  # cut in the middle of a sample
    assert 790 <= len(out) <= 810  # 0.1 s at 8 kHz, 1 byte per sample


def test_twiml_streams_to_the_public_url_with_the_token(monkeypatch):
    monkeypatch.setattr(phone, "stream_url", lambda: "wss://demo.example/phone/media")
    xml = phone.twiml('tok"&<')
    assert '<Stream url="wss://demo.example/phone/media">' in xml
    assert "&amp;&lt;" in xml and 'tok"&<' not in xml  # escaped
    assert xml.startswith("<?xml") and "<Connect>" in xml


def test_mask_keeps_only_the_ends():
    assert phone.mask("+33612345678") == "+336 •• •• •• 78"


async def test_agent_audio_becomes_twilio_media_marks_and_clear():
    ws = FakeTwilioWS()
    conn = phone.PhoneConnection(ws)  # type: ignore[arg-type]
    conn.stream_sid = "MZ1"
    screen = phone.watch()
    try:
        await conn.send(AgentAudioStartMessage(mime="audio/x-mulaw"))
        assert conn.agent_playing
        await conn.send_bytes(b"\xff" * (phone.MEDIA_CHUNK + 10))
        await conn.send(AgentAudioEndMessage())
        media = [e for e in ws.sent if e["event"] == "media"]
        assert len(media) == 2 and all(e["streamSid"] == "MZ1" for e in media)
        assert base64.b64decode(media[1]["media"]["payload"]) == b"\xff" * 10
        mark = ws.sent[-1]
        assert mark["event"] == "mark" and conn.unplayed == {mark["mark"]["name"]}

        conn.on_mark(mark["mark"]["name"])  # Twilio: the audio has been played
        assert not conn.agent_playing and not conn.unplayed

        await conn.send(CallerSpeakingMessage())  # barge-in: Twilio drops what is not played yet
        assert ws.sent[-1] == {"event": "clear", "streamSid": "MZ1"}

        await conn.send(TranscriptMessage(text="Allô ?", final=True))  # not for Twilio: for the screen
        assert ws.sent[-1]["event"] == "clear"
        seen = [json.loads(screen.get_nowait()) for _ in range(screen.qsize())]
        assert [m["type"] for m in seen] == ["caller_speaking", "transcript"]
    finally:
        phone.unwatch(screen)


async def test_hang_up_waits_for_the_last_words_to_be_played():
    conn = phone.PhoneConnection(FakeTwilioWS())  # type: ignore[arg-type]
    conn.unplayed = {"m3"}
    conn.closing = True
    conn.on_mark("m2")  # an older mark: still playing
    assert conn.hanging_up is None
    conn.on_mark("m3")
    assert conn.hanging_up is not None
    conn.cancel_hang_up()  # the caller spoke again
    await asyncio.sleep(0)
    assert conn.hanging_up.cancelled() and not conn.closing


async def test_unanswered_call_is_recorded_as_no_answer(monkeypatch):
    patched = []

    async def fake_patch(self, path, body=None):
        patched.append((path, body))

    monkeypatch.setattr(phone.ErpClient, "patch", fake_patch)
    phone.calls["t1"] = phone.PhoneCall("ccl_1", "t1", "CA1")
    screen = phone.watch()
    try:
        await phone.on_status("t1", "ringing")  # not final: nothing
        assert "t1" in phone.calls
        await phone.on_status("t1", "no-answer")
        assert "t1" not in phone.calls
        assert patched[0][0] == "/api/campaign-calls/ccl_1" and patched[0][1]["status"] == "no_answer"
        assert json.loads(screen.get_nowait()) == CallEndedMessage(call_id="ccl_1", outcome="no_answer").to_wire()
        await phone.on_status("unknown", "no-answer")  # not ours: ignored
        assert len(patched) == 1
    finally:
        phone.unwatch(screen)
        phone.calls.clear()
