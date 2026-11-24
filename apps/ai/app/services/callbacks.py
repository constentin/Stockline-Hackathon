"""
Automatic callbacks: when a client asked to be called back (« rappelez-moi demain à 15 h », « dans 10 minutes »), the
agent recorded it (record_call_outcome callback, callbackAt in the company's local time). This loop rings it at
that time.

    every POLL_S  → GET /api/campaign-calls/callbacks       (ERP: due now / upcoming, company local time)
    a call is due → phone (Twilio) configured and free?     → services/phone.place_call: your phone rings, the
                                                              agent picks the conversation up where it was left
                    no phone                                → nothing here: the web shows a banner « Rappel prévu
                                                              maintenant » with a button (a browser can't start
                                                              audio without a click)

Only FRESH callbacks are rung by themselves (due for less than VOICE_CALLBACK_WINDOW_MIN minutes): an old one left
over from yesterday's tests never makes a phone ring by surprise; it stays listed as late, to start by hand.
One call at a time; a callback tried is not retried for RETRY_S (no answer → the ERP records "no_answer").
VOICE_CALLBACK_AUTO=0 turns the automatic ringing off.
"""

import asyncio
import time
from datetime import datetime

from app.config import settings
from app.tools import ErpClient

POLL_S = 20
RETRY_S = 600

_tried: dict[str, float] = {}  # campaign call id → when it was rung


def minutes_late(callback_at: str, now: str) -> float:
    try:
        return (datetime.fromisoformat(now[:16]) - datetime.fromisoformat(callback_at[:16])).total_seconds() / 60
    except ValueError:
        return 1e9


def busy() -> bool:
    """A call is already going on (phone, or an outbound call in a browser)."""
    from . import phone
    from .voice_session import conversations

    return phone.active_call() is not None or any(c.call for c in conversations.values())


async def ring_due() -> str | None:
    """One pass: ring the first fresh due callback. Returns its call id (None = nothing rung)."""
    from . import phone

    if not settings.voice.callback_auto or phone.disabled_reason() or busy():
        return None
    data = await ErpClient("voice_agent", "Rappels automatiques").get("/api/campaign-calls/callbacks")
    for c in data.get("due", []):
        late = minutes_late(c.get("callbackAt") or "", data["now"])
        if late > settings.voice.callback_window_min or time.monotonic() - _tried.get(c["id"], -1e9) < RETRY_S:
            continue
        _tried[c["id"]] = time.monotonic()
        print(f"[rappels] rappel prévu à {c['callbackAt'][11:16]} pour {c.get('clientName')} : le téléphone sonne", flush=True)
        await phone.place_call(c["id"])
        return c["id"]
    return None


async def run() -> None:
    """The loop started with the service (main.py). Never raises."""
    while True:
        await asyncio.sleep(POLL_S)
        try:
            await ring_due()
        except Exception as e:  # ERP down, Twilio refused…: try again at the next pass
            print(f"[rappels] {type(e).__name__}: {e}", flush=True)
