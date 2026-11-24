"""
What the caller really HEARD of the agent's reply, when he cut it.

The model writes its reply in under a second; the voice takes 5–10 s to say it. If the caller speaks over it after
2 s, the history must not hold the whole reply as « said »: the agent would think he heard the price, the recap, the
question « Je valide ? »… and a « oui » that follows would confirm an order he never heard.

    Reply             one agent turn's audio, sentence by sentence: where each sentence starts and ends in the
                      audio segment (from the bytes sent), and which ones the phone reported as played
    cut_at(seconds)   browser: the page says how far it had played when it stopped (`playback` with playedMs)
    played(index)     phone: Twilio echoes a mark after each sentence once it has been played
    rewrite(history)  the turn's agent text is cut where the caller stopped hearing it, and a switchboard note says
                      what he did NOT hear: the agent answers him first, then gives back in one sentence what still
                      matters (agent/prompts.py)

A sentence cut in the middle counts as not heard. Waiting phrases are not in the history: they are skipped.
"""

import re
from dataclasses import dataclass, field

from app.schemas import ChatMessage

NOTE_SEP = "\n[Standard"  # same marker as services/outbound.py: stripped from transcripts, never read aloud
TAIL_S = 0.25  # stopping this close to the end = it was all heard


@dataclass
class Item:
    text: str
    filler: bool  # a pre-recorded waiting phrase (not in the history)
    start_b: int = 0  # bytes into the turn's audio segment
    end_b: int | None = None  # None: not fully sent


@dataclass
class Reply:
    """One agent turn's audio, for knowing what the caller heard if he cuts it."""

    user_text: str  # the caller's words this turn answers (to find the turn in the history)
    bytes_per_s: float = 16000.0  # mp3 128 kb/s (browser) or µ-law 8 kHz (phone): set from the audio mime
    seq: int = 0  # turn number (phone marks carry it)
    items: list[Item] = field(default_factory=list)
    sent_bytes: int = 0
    played: set[int] = field(default_factory=set)  # phone: sentences Twilio reported as played
    playback_started: bool = False  # browser: the agent's audio of this turn started playing
    cut_s: float | None = None  # browser: how far it played when it was stopped
    cut_by_marks: bool = False  # phone: the caller spoke over it (clear sent): heard = the played marks
    applied: bool = False

    # ── recording, as the audio is sent ──

    def begin(self, text: str, filler: bool) -> int:
        self.items.append(Item(text, filler, start_b=self.sent_bytes))
        return len(self.items) - 1

    def add(self, n_bytes: int) -> None:
        self.sent_bytes += n_bytes

    def end(self, index: int) -> None:
        self.items[index].end_b = self.sent_bytes

    @property
    def total_s(self) -> float:
        return self.sent_bytes / self.bytes_per_s

    # ── what was heard ──

    @property
    def cut(self) -> bool:
        """The caller did not hear the whole reply."""
        if self.cut_by_marks:
            return any(i not in self.played for i in range(len(self.items)))
        return self.cut_s is not None

    def heard(self) -> list[Item]:
        """The sentences fully heard, in order (stops at the first one not heard)."""
        out = []
        for i, item in enumerate(self.items):
            if self.cut_by_marks:
                ok = i in self.played
            else:
                ok = item.end_b is not None and self.cut_s is not None and item.end_b / self.bytes_per_s <= self.cut_s + 0.05
            if not ok:
                break
            out.append(item)
        return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def rewrite(history: list[ChatMessage], reply: Reply) -> str | None:
    """Cut the turn's agent text where the caller stopped hearing it; add a note with what he did not hear.
    Returns the unheard text (None: nothing to change). Changes `history` in place."""
    turn_start = next(
        (k for k in range(len(history) - 1, -1, -1) if history[k].role == "user" and reply.user_text and reply.user_text in (history[k].content or "")),
        None,
    )  # fmt: skip
    if turn_start is None:
        return None
    msgs = [k for k in range(turn_start + 1, len(history)) if history[k].role == "assistant"]
    if not msgs:
        return None
    contents = [(history[k].content or "").split(NOTE_SEP)[0] for k in msgs]
    full = "".join(contents)
    # how far the heard sentences go into the agent's text (waiting phrases are not in it)
    cursor = 0
    for item in reply.heard():
        if item.filler:
            continue
        words = _norm(item.text)
        pos = _norm(full[cursor:]).find(words)
        if pos < 0:
            break
        # map back to the raw text: advance over the same number of non-space characters
        target = len(re.sub(r"\s", "", _norm(full[cursor:])[: pos + len(words)]))
        seen = 0
        while cursor < len(full) and seen < target:
            if not full[cursor].isspace():
                seen += 1
            cursor += 1
    if not _norm(full[cursor:]):
        return None
    left, pieces = cursor, []
    for k, c in zip(msgs, contents):
        m = history[k]
        keep = c if left >= len(c) else (c[:left].rstrip() + " …" if left > 0 else "")
        pieces.append(c[min(left, len(c)):])
        left = max(0, left - len(c))
        history[k] = ChatMessage(role="assistant", content=keep, tool_calls=m.tool_calls)
    unheard = _norm(" ".join(pieces))
    last = msgs[-1]
    note = (f"{NOTE_SEP} : le client t'a coupée ; il n'a PAS entendu : « {unheard[:600]} ». Ne le tiens pas pour dit : "
            "réponds d'abord à ce qu'il vient de dire, puis redonne en une phrase ce qui compte encore.]")  # fmt: skip
    history[last] = ChatMessage(role="assistant", content=f"{history[last].content or '…'}{note}", tool_calls=history[last].tool_calls)
    return unheard


def bytes_per_second(mime: str) -> float:
    """Playing rate of the agent's audio: µ-law 8 kHz (phone) or mp3 128 kb/s (every TTS for the browser)."""
    return 8000.0 if "mulaw" in mime or "basic" in mime else 16000.0
