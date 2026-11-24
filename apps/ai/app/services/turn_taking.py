"""
Turn-taking rules for hands-free calls: what a caller's words MEAN for the conversation, before any model.

    hesitation   « euh », « hum », « bah »…          never counts as words: alone, it is not a turn, it cuts nothing
    backchannel  « oui », « d'accord », « mmh »…     said while the agent talks = "I'm listening": the agent goes on
                                                     (unless the agent had just asked a question: then it IS the answer,
                                                     taken once the agent has finished speaking)
    hesitant end « … et », « … mais », « … euh »     the sentence is not over: wait longer (VOICE_HOLD_MS)
    barge-in     ≥ VOICE_BARGE_IN_WORDS real words   « attendez, non » over the agent: it stops and listens

Plain word lists on the transcript: no model call, no latency. Used by services/listener.py (when a sentence ends)
and services/voice_session.py (barge-in, which turns to answer). Tune with the VOICE_* settings in .env.
"""

import re

from app.config import settings

HESITATIONS = {
    "euh", "euhh", "euuh", "heu", "heuu", "hum", "humm", "hmm", "hm", "mmh", "mh", "mm", "mmm", "bah", "ben", "beh",
    "ah", "oh", "eh", "hein", "pff", "bof", "ouf", "bon", "alors", "genre", "enfin", "voilà", "quoi", "là",
}  # fmt: skip
# Whole utterances that only say "I'm listening" (hesitations removed first)
BACKCHANNELS = {
    "oui", "ouais", "ouai", "oui oui", "ouais ouais", "d'accord", "ok", "okay", "ok d'accord", "oui d'accord",
    "d'accord d'accord", "entendu", "je vois", "ah oui", "ah d'accord", "ah ok", "c'est ça", "exactement", "tout à fait",
    "très bien", "ça marche", "mmh mmh", "hum hum", "oui je vois", "ah bon", "si", "voilà",
}  # fmt: skip
# A sentence ending on these is not over (« je voudrais… euh », « des moules et »)
HESITANT_ENDS = {
    "et", "mais", "ou", "donc", "parce", "que", "qui", "de", "du", "des", "le", "la", "les", "un", "une", "à", "au",
    "aux", "pour", "avec", "sur", "en", "je", "j", "il", "on", "vous", "si", "comme", "puis", "plus", "moins",
    "voudrais", "veux", "prends", "prendrais", "mettez", "mets", "faut", "fallait",
} | HESITATIONS  # fmt: skip

_word = re.compile(r"[a-zà-ÿ0-9]+(?:'[a-zà-ÿ]+)?")


def words(text: str) -> list[str]:
    return _word.findall(text.lower().replace("’", "'"))


def content_words(text: str) -> list[str]:
    """The words that carry meaning (hesitations removed)."""
    return [w for w in words(text) if w not in HESITATIONS]


def only_hesitation(text: str) -> bool:
    """« Euh… », « Hum. », « bah euh »: nothing said yet."""
    return not content_words(text)


def is_backchannel(text: str) -> bool:
    """« Oui oui. », « D'accord. », « Mmh mmh »: the caller is just listening."""
    if not settings.voice.ignore_backchannel:
        return False
    w = words(text)
    core = " ".join(x for x in w if x not in HESITATIONS - {"voilà"}) or " ".join(w)
    return core in BACKCHANNELS


def hesitant_end(text: str) -> bool:
    """The sentence stops mid-way: « je voudrais des moules et », « 20 kilos euh », « c'est pour… »."""
    if text.rstrip().endswith(("…", "...", ",")):
        return True
    w = words(text)
    return bool(w) and w[-1] in HESITANT_ENDS


def is_barge_in(text: str) -> bool:
    """Enough real words over the agent to stop it (and not a mere « oui, d'accord »)."""
    return len(content_words(text)) >= settings.voice.barge_in_words and not is_backchannel(text)


CHARS_PER_S = 15.0  # speaking rate of the TTS voices (measured: ~230 characters in ~15 s)


def asks_question(agent_text: str) -> bool:
    """The agent's reply ends with a question: a « oui » over its end is an answer, not a backchannel."""
    return agent_text.rstrip().rstrip("»\"' ").endswith("?")


def during_final_question(agent_text: str, playing_for_s: float) -> bool:
    """Was the agent already saying its final question when the caller spoke? Estimated from how long its audio had
    been playing: « oui » during « …Ça vous intéresse ? » answers it; « oui oui » during the offers is just listening."""
    if not asks_question(agent_text):
        return False
    text = agent_text.strip()
    cut = max(text.rfind(". ", 0, len(text) - 1), text.rfind("! ", 0, len(text) - 1), text.rfind("\n", 0, len(text) - 1))
    question_starts = cut + 1 if cut >= 0 else 0
    return playing_for_s * CHARS_PER_S >= question_starts - 15  # a little margin: people answer a bit early
