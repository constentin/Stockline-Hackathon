"""
Chat with the agent in the terminal — the same agent as POST /chat and WS /voice.

    uv run python -m app.cli            (chat mode)
    uv run python -m app.cli --voice    (short spoken-style answers)
    uv run python -m app.cli --call ccl_000001   (outbound clearance call: the agent speaks first, you play the client)

Commands: /reset clears the conversation, /exit quits (with --call: /exit = hang up, the outcome is recorded).
"""

import asyncio
import json
import sys

from app.agent import run_agent
from app.clients import get_llm_client
from app.schemas import ChatMessage, DoneEvent, ErrorEvent, TextEvent, ToolCallEvent, ToolResultEvent
from app.services.outbound import OPENING_CUE, end_call, start_call
from app.tools import all_tools, outbound_tools


def dim(s: str) -> str:
    return f"\x1b[2m{s}\x1b[0m"


def arg(name: str) -> str | None:
    """Value after a flag: --call ccl_000001."""
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv[:-1] else None


async def turn(messages: list[ChatMessage], llm, tools, mode: str, extra: str | None) -> None:
    print("agent › ", end="", flush=True)
    async for ev in run_agent(messages, llm=llm, tools=tools, mode=mode, actor_name="CLI", system_extra=extra):
        match ev:
            case TextEvent(delta=delta):
                print(delta, end="", flush=True)
            case ToolCallEvent(call=call):
                print(dim(f"\n  → {call.name} {json.dumps(call.arguments, ensure_ascii=False)}"))
            case ToolResultEvent(ok=ok, ms=ms, result=result):
                print(dim(f"  ← {'ok' if ok else 'ERREUR'} {ms}ms {json.dumps(result, ensure_ascii=False)[:160]}"))
            case DoneEvent(ms=ms, usage=usage):
                cache = f", cache lu {usage.cache_read_input_tokens} / écrit {usage.cache_creation_input_tokens or 0}" if usage.cache_read_input_tokens is not None else ""
                print(dim(f"\n  ({ms}ms, {usage.input_tokens or 0}→{usage.output_tokens or 0} tokens{cache})"))
            case ErrorEvent(message=message):
                print(f"\n[erreur] {message}")


async def main() -> None:
    call_id = arg("--call")
    mode = "outbound" if call_id else "voice" if "--voice" in sys.argv else "chat"
    llm = get_llm_client()
    messages: list[ChatMessage] = []
    tools, extra, call = all_tools(), None, None
    print(dim(f"llm={llm.provider}:{llm.model} mode={mode} — /reset, /exit"))
    if call_id:
        call = await start_call(call_id, "CLI")
        tools, extra = outbound_tools(), call.prompt
        offers = ", ".join(f"{o['productName']} ({o['remaining']} {o['unit']})" for o in call.brief.get("offers", []))
        print(dim(f"appel {call_id} → {call.client_name} — offres : {offers or 'aucune'}"))
        messages.append(ChatMessage(role="user", content=OPENING_CUE))
        await turn(messages, llm, tools, mode, extra)
    while True:
        try:
            line = (await asyncio.to_thread(input, "\nvous › ")).strip()
        except (EOFError, KeyboardInterrupt):
            break
        if line == "/exit":
            break
        if line == "/reset":
            messages = []
            print(dim("(conversation effacée)"))
            continue
        if not line:
            continue
        messages.append(ChatMessage(role="user", content=line))
        await turn(messages, llm, tools, mode, extra)
    if call:
        outcome = await end_call(call, messages, "CLI")
        result = "déjà enregistré par l'agent" if outcome is None else f"enregistré au raccrocher : {outcome}"
        print(dim(f"(raccroché — résultat {result})"))


if __name__ == "__main__":
    asyncio.run(main())
