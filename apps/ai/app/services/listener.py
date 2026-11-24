"""
Hands-free mode: the client's mic streams the whole call, no key to hold. This decides WHEN he has finished.

    listen_start → Listener(stt, mime, hint, …)   one live STT stream for the call (stt.listen)
    binary frame → feed(frame)
      words heard   → on_words(text)               the voice session shows them, and handles barge-in
      end of turn   → on_turn(text, last_word_at)  the voice session runs the agent turn
    listen_stop  → close()

End of turn ("endpointing"), checked every 50 ms:
  1. The words stop changing. After QUIET_S the router (Jev, services/router.py) is asked — once per text — whether
     the sentence is finished; the same answer serves the routing of the turn, so it is free.
  2. Sentence finished (complete ≥ 0.75) → finalize after QUIET_S; unsure → after SILENCE_S; clearly cut in the
     middle ("je voudrais… euh") → wait up to HOLD_S for the rest.
  3. finalize = ask the STT to make the last words final (a few hundred ms); its answer ends the sentence and the
     turn starts. A pause the STT detects by itself ends it too (same rules for the hold).
The turn's t0 is the last time the words changed (≈ the end of speech), so the measured latency includes this wait.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable

from app.clients import STTClient

from .router import Route, same_words

QUIET_S = 0.25  # words unchanged this long: ask Jev if the sentence is finished
SILENCE_S = 0.6  # unsure: end of turn after this silence
HOLD_S = 1.6  # sentence cut in the middle: wait this long for the rest
TICK_S = 0.05


class Listener:
    def __init__(
        self,
        stt: STTClient,
        mime: str,
        hint: str | None,
        on_words: Callable[[str, str], Awaitable[None]],  # (text, stable)
        on_turn: Callable[[str, float, Route | None], Awaitable[None]],  # (text, last_word_at, route)
        judge: Callable[[str], Awaitable[Route | None]],  # the Jev router, on the text heard so far
        on_stopped: Callable[[str], Awaitable[None]] | None = None,  # the STT stream failed (reason)
    ):
        self.stt, self.on_words, self.on_turn, self.judge, self.on_stopped = stt, on_words, on_turn, judge, on_stopped
        self.frames: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.control: asyncio.Queue[str] = asyncio.Queue()
        self.done = ""  # finished sentences of the current turn
        self.current = ""  # the sentence being heard (provisional words included)
        self.changed_at = time.perf_counter()
        self.finalizing = False
        self.judged: dict[str, asyncio.Task] = {}  # text → Jev route (one call per text)
        self.stream = asyncio.create_task(self._stream(mime, hint))
        self.watch = asyncio.create_task(self._watch())

    def feed(self, frame: bytes) -> None:
        self.frames.put_nowait(frame)

    async def close(self) -> None:
        self.frames.put_nowait(None)
        for t in (self.watch, self.stream, *self.judged.values()):
            t.cancel()
        for t in (self.watch, self.stream):
            try:
                await t
            except BaseException:  # noqa: S110  (cancelled, or the stream had failed: nothing to do)
                pass

    @property
    def heard(self) -> str:
        return " ".join(x for x in (self.done, self.current) if x).strip()

    # ── STT stream ──

    async def _audio(self) -> AsyncIterator[bytes]:
        while (frame := await self.frames.get()) is not None:
            yield frame

    async def _stream(self, mime: str, hint: str | None) -> None:
        try:
            async for u in self.stt.listen(self._audio(), mime, hint, self.control):
                if u.final:
                    self.done = f"{self.done} {u.text}".strip()
                    self.current, self.finalizing = "", False
                elif u.text != self.current:
                    self.current = u.text
                    self.changed_at = time.perf_counter()
                    self.finalizing = False
                else:
                    continue
                if self.heard:
                    await self.on_words(self.heard, self.done)
        except Exception as e:
            print(f"[ai] hands-free STT stopped ({type(e).__name__}: {e})", flush=True)
            self.watch.cancel()
            if self.on_stopped:
                await self.on_stopped(str(e) or type(e).__name__)

    # ── end of turn ──

    def _route(self, text: str) -> asyncio.Task:
        """The Jev task for this text, reusing one on the same words (the final words often differ only in case or
        punctuation from the provisional ones)."""
        for k, task in self.judged.items():
            if same_words(k, text):
                return task
        if text not in self.judged:
            self.judged = {k: v for k, v in self.judged.items() if v.done() or k == text} | {text: asyncio.create_task(self.judge(text))}
        return self.judged[text]

    async def _watch(self) -> None:
        while True:
            await asyncio.sleep(TICK_S)
            text = self.heard
            if not text:
                continue
            quiet = time.perf_counter() - self.changed_at
            if quiet < QUIET_S:
                continue
            task = self._route(text)
            route = task.result() if task.done() and not task.cancelled() and not task.exception() else None
            complete = route.complete if route else None
            wait = QUIET_S if complete is not None and complete >= 0.75 else HOLD_S if complete is not None and complete < 0.3 else SILENCE_S
            if quiet < wait:
                continue
            if self.current and not self.finalizing:  # provisional words left: make them final first
                self.finalizing = True
                self.control.put_nowait(self.stt.finalize_message())
                continue
            if self.current:  # waiting for the STT's final words
                if quiet < wait + 1.5:
                    continue
                self.done, self.current = text, ""  # no answer to finalize: take what was heard
            turn, last_word_at = self.done, self.changed_at
            self.done, self.judged = "", {}
            print(f"[ai] fin de tour après {int(quiet * 1000)} ms de silence (phrase finie : "
                  f"{'?' if complete is None else round(complete, 2)}, attente prévue {int(wait * 1000)} ms)", flush=True)  # fmt: skip
            await self.on_turn(turn, last_word_at, route if route and same_words(route.text, turn) else None)
