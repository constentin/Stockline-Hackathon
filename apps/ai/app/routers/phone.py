"""
Real phone calls through Twilio (logic: app/services/phone.py, setup: GUIDE-TELEPHONE.md).

    GET  /phone           can the agent call a phone? (the web shows the button when enabled)
    POST /phone/call      ring TWILIO_TO_NUMBER for a campaign call
    POST /phone/hangup    hang up the call in progress
    POST /phone/status    Twilio's status callback (not answered → no_answer)       ← called by Twilio
    WS   /phone/media     Twilio Media Streams: the call's audio, both ways          ← called by Twilio
    WS   /voice/phone     the web pages watching the call live (under /voice so the Vite WebSocket proxy forwards it)
"""

import asyncio
from urllib.parse import parse_qs

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response

from app.config import settings
from app.schemas import ErrorResponse
from app.schemas.phone import PhoneCallRequest, PhoneCallResponse, PhoneInfo
from app.services import phone
from app.services.phone import PhoneConnection, PhoneError

router = APIRouter(tags=["phone"])


@router.get("/phone", response_model=PhoneInfo)
async def phone_info() -> PhoneInfo:
    reason = phone.disabled_reason()
    to = phone.mask(settings.twilio.to_number) if settings.twilio.to_number else None
    return PhoneInfo(enabled=reason is None, to=to, reason=reason)


@router.post("/phone/call", response_model=PhoneCallResponse, responses={400: {"model": ErrorResponse}})
async def phone_call(body: PhoneCallRequest):
    try:
        call = await phone.place_call(body.campaign_call_id)
    except PhoneError as e:
        return JSONResponse(ErrorResponse.of("phone_error", str(e)), status_code=400)
    return PhoneCallResponse(call_sid=call.call_sid, to=phone.mask(settings.twilio.to_number))


@router.post("/phone/hangup", status_code=204, responses={400: {"model": ErrorResponse}})
async def phone_hangup(body: PhoneCallRequest):
    call = phone.active_call()
    if not call or call.campaign_call_id != body.campaign_call_id:
        return Response(status_code=204)  # already over
    try:
        await phone.end_phone_call(call)
    except PhoneError as e:
        return JSONResponse(ErrorResponse.of("phone_error", str(e)), status_code=400)
    return Response(status_code=204)


@router.post("/phone/status", include_in_schema=False)
async def phone_status(request: Request, token: str = ""):
    """Twilio posts the call's final status (form-encoded). The token identifies a call this service placed."""
    form = parse_qs((await request.body()).decode())
    await phone.on_status(token, (form.get("CallStatus") or [""])[0])
    return Response(status_code=204)


@router.websocket("/phone/media")
async def phone_media(ws: WebSocket):
    await PhoneConnection(ws).serve()


@router.websocket("/voice/phone")
async def phone_watch(ws: WebSocket):
    """Every message of the phone call in progress (the same JSON as WS /voice, without the audio)."""
    await ws.accept()
    queue = phone.watch()

    async def pump() -> None:
        while True:
            await ws.send_text(await queue.get())

    task = asyncio.create_task(pump())
    try:
        while (await ws.receive())["type"] != "websocket.disconnect":
            pass  # the page sends nothing: we only wait for it to leave
    except WebSocketDisconnect:
        pass
    finally:
        task.cancel()
        phone.unwatch(queue)
