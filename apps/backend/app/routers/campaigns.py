"""/api/campaigns (clearance campaigns: import, detail, next call) and /api/campaign-calls (brief, quote, accept, outcome)."""

from fastapi import APIRouter, Query

from app import crud
from app.dependencies import CtxDep
from app.schemas.inputs import CampaignAcceptInput, CampaignCallPatch, CampaignImportInput, CampaignPatch
from app.schemas.views import (
    CallBrief, CampaignAcceptResult, CampaignCallView, CampaignDetail, CampaignQuote, CampaignSummary, ClearanceProposal, NextCall,
)
from app.services import campaigns, clearance

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])
calls = APIRouter(prefix="/api/campaign-calls", tags=["campaigns"])


@router.get("", response_model=list[CampaignSummary])
async def list_campaigns():
    """Newest first, with their stats."""
    return campaigns.list_campaigns()


@router.post("/import", response_model=CampaignDetail, status_code=201)
async def import_campaign(body: CampaignImportInput, ctx: CtxDep):
    """The clearance model's output → a campaign + one call per client (skipped: blocked, do-not-call, expires before delivery)."""
    return campaigns.import_campaign(ctx, body.data())


@router.get("/proposal", response_model=ClearanceProposal)
async def proposal():
    """The clearance model: expected losses (FEFO simulation), candidate clients, the discount that recovers the most.
    `campaign` is ready for POST /import (nothing is written here). See app/services/clearance.py."""
    return clearance.proposal()


@router.get("/{id}", response_model=CampaignDetail)
async def get_campaign(id: str):
    return campaigns.campaign_detail(id)


@router.patch("/{id}", response_model=CampaignDetail)
async def update_campaign(id: str, body: CampaignPatch, ctx: CtxDep):
    """Rename, or close (status "closed": no more calls, no more sales)."""
    return campaigns.update_campaign(ctx, id, body.data())


@router.get("/{id}/next-call", response_model=NextCall)
async def next_call(id: str):
    """Best-ranked call to make now (pending, or a callback that is due) with an offer still open; no `call` = done."""
    return campaigns.next_call(id)


@calls.get("/{id}", response_model=CampaignCallView)
async def get_call(id: str):
    return campaigns.call_view(crud.campaign_calls.require(id))


@calls.get("/{id}/brief", response_model=CallBrief)
async def call_brief(id: str):
    """What the voice agent needs for this call: client, open offers with usual / offer / floor prices, context."""
    return campaigns.call_brief(id)


@calls.get("/{id}/quote", response_model=CampaignQuote)
async def quote(
    id: str,
    offer_id: str = Query(..., alias="offerId"),
    pct: float | None = Query(None, description="Discount %, default = the offer's opening discount; refused beyond the max"),
    qty: float | None = None,
):
    return campaigns.quote(id, offer_id, pct, qty)


@calls.post("/{id}/accept", response_model=CampaignAcceptResult)
async def accept_offer(id: str, body: CampaignAcceptInput, ctx: CtxDep):
    """The client accepts an offer: a line on the call's draft order at the discounted price (same offer again = update)."""
    return campaigns.accept_offer(ctx, id, body.data())


@calls.patch("/{id}", response_model=CampaignCallView)
async def update_call(id: str, body: CampaignCallPatch, ctx: CtxDep):
    """status in_progress (call starts) or an outcome (call ends: logged in the client's history), summary, callbackAt."""
    return campaigns.update_call(ctx, id, body.data())
