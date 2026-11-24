"""What the caller really heard when he cut the agent (services/heard.py), offline."""

from app.schemas import ChatMessage, ToolCall
from app.services.heard import Reply, rewrite

ACK = "Vingt kilos de moules, je regarde le prix."
MAIN = "Vingt kilos de moules de bouchot, 59 euros 60, livrés mercredi. Je valide ?"


def history() -> list[ChatMessage]:
    return [
        ChatMessage(role="assistant", content="Bonjour Manon ! Moules à 2 euros 98. Ça vous dit ?"),
        ChatMessage(role="user", content="Vingt kilos de moules.\n[Standard : il parle sans doute de : moules (off_2).]"),
        ChatMessage(role="assistant", content=ACK, tool_calls=[ToolCall(id="t1", name="accept_offer", arguments={"qty": 20})]),
        ChatMessage(role="tool", tool_call_id="t1", name="accept_offer", content="{}"),
        ChatMessage(role="assistant", content=MAIN),
    ]


def reply(*sentences: tuple[str, bool, int]) -> Reply:
    """(text, is a waiting phrase, bytes) in the order they were sent; 16 000 bytes = 1 s of mp3."""
    r = Reply(user_text="Vingt kilos de moules.")
    for text, filler, n in sentences:
        i = r.begin(text, filler)
        r.add(n)
        r.end(i)
    return r


SENT = [("Je note.", True, 8000), (ACK, False, 40000), ("Vingt kilos de moules de bouchot, 59 euros 60, livrés mercredi.", False, 64000),
        ("Je valide ?", False, 16000)]  # fmt: skip


def test_cut_during_the_price_keeps_only_what_was_heard():
    h = history()
    r = reply(*SENT)
    r.cut_s = 4.0  # the filler (0.5 s) and the acknowledgement (to 3 s) were heard; cut in the price
    unheard = rewrite(h, r)
    assert unheard == MAIN
    assert h[2].content == ACK and h[2].tool_calls  # the tool call is kept (the order line exists)
    assert h[4].content.startswith("…") and "il n'a PAS entendu : « " + MAIN in h[4].content
    assert "Je valide ?" not in h[4].content.split("\n[Standard")[0]  # the code will not take a « oui » for a yes to it


def test_cut_before_the_question_only():
    h = history()
    r = reply(*SENT)
    r.cut_s = 7.2  # everything but « Je valide ? »
    assert rewrite(h, r) == "Je valide ?"
    said = h[4].content.split("\n[Standard")[0]
    assert "59 euros 60" in said and "valide" not in said


def test_nothing_heard_and_all_heard():
    h = history()
    r = reply(*SENT)
    r.cut_s = 0.0
    assert rewrite(h, r) == f"{ACK} {MAIN}"  # nothing of the turn was heard
    h2 = history()
    r2 = reply(*SENT)
    r2.cut_s = 99.0
    assert rewrite(h2, r2) is None and h2 == history()  # heard to the end: nothing changes


def test_phone_marks():
    h = history()
    r = reply(*SENT)
    r.cut_by_marks = True
    r.played = {0, 1}  # Twilio echoed the filler and the acknowledgement only
    assert r.cut and rewrite(h, r) == MAIN
    r2 = reply(*SENT)
    r2.cut_by_marks, r2.played = True, {0, 1, 2, 3}
    assert not r2.cut  # all played: not a cut
