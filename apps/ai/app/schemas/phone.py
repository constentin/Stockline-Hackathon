"""Phone calls through Twilio (routers/phone.py, services/phone.py): the HTTP bodies the web uses."""

from .base import WireModel


class PhoneInfo(WireModel):
    """GET /phone: can the agent call a real phone, and which one (masked)."""

    enabled: bool
    to: str | None = None  # the number called, masked ("+33 6 •• •• •• 78")
    reason: str | None = None  # why it is disabled (missing settings, unsupported TTS / STT)


class PhoneCallRequest(WireModel):
    """POST /phone/call: ring TWILIO_TO_NUMBER for this campaign call. POST /phone/hangup: end it."""

    campaign_call_id: str


class PhoneCallResponse(WireModel):
    call_sid: str
    to: str  # masked
