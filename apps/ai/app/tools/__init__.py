"""
The tools the model can call, grouped by domain. Each one wraps ERP API endpoints.

    registry.py  @tool decorator, Tool, ToolContext (start here to add a tool)
    erp.py       the HTTP client for the ERP API
    catalog.py   search, get_client, get_product_stock, find_alternatives, get_price
    context.py   search_context, get_context, save_context
    history.py   get_client_history, log_client_event, get_supplier_history, log_supplier_event
    orders.py    create_draft_order, add_order_line, confirm_order, get_order
    campaign.py  get_call_brief, quote_offer, accept_offer, record_call_outcome (outbound clearance calls)

A new file needs one more line: import it below so its @tool functions get registered.
"""

from . import campaign, catalog, context, history, orders  # noqa: F401  (importing registers the tools)
from .erp import ErpClient
from .registry import TOOLS, Tool, ToolContext, tool


# Outbound clearance calls: fewer tools = a shorter prompt, a faster first word and less hesitation.
OUTBOUND_TOOL_NAMES = (
    "get_call_brief", "quote_offer", "accept_offer", "confirm_call_order", "record_call_outcome",
    "get_order", "save_context", "log_client_event",
)  # fmt: skip
CAMPAIGN_TOOL_NAMES = ("get_call_brief", "quote_offer", "accept_offer", "confirm_call_order", "record_call_outcome")


def all_tools() -> list[Tool]:
    """Every tool except the campaign ones (they need a call in progress: see outbound_tools)."""
    return [t for name, t in TOOLS.items() if name not in CAMPAIGN_TOOL_NAMES]


def outbound_tools() -> list[Tool]:
    return [TOOLS[name] for name in OUTBOUND_TOOL_NAMES]


__all__ = ["TOOLS", "ErpClient", "Tool", "ToolContext", "all_tools", "outbound_tools", "tool"]
