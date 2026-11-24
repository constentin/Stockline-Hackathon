"""
Text → what the voice should SAY. Applied to every sentence just before the TTS (Speaker), never to what is shown.

Most mispronunciations come from the written form, not from the voice model: "15,73 €", "10 kg", "HT", "30/40",
"n°", "-20 %". Rewriting them in words is deterministic, costs nothing and works with every TTS provider.

    for_speech("10 kg à 15,73 € HT (-20 %)")  →  "10 kilos à 15 euros 73 hors taxes (moins 20 pour cent)"

LEXICON: words a voice says wrong, written the way they sound. Add the ones you hear during your calls
(test them with `python -m app.eval_tts`).
"""

import re

LEXICON: dict[str, str] = {
    # written form (case-insensitive, whole word) → how to say it
    "HT": "hors taxes",
    "TTC": "toutes taxes comprises",
    "n°": "numéro",
    "N°": "numéro",
    "SARL": "S A R L",
    "SAS": "S A S",
    "EARL": "E A R L",
}

UNITS = {  # after a number
    "kg": "kilos",
    "g": "grammes",
    "l": "litres",
    "L": "litres",
    "cl": "centilitres",
    "pcs": "pièces",
    "pce": "pièce",
    "pces": "pièces",
    "u": "unités",
}

NUM = r"\d+(?:[  ]\d{3})*"  # 1 200 (thin / no-break spaces too)

_money_cents = re.compile(rf"({NUM})[,.](\d{{1,2}})\s*€")  # 15,73 € → 15 euros 73
_money = re.compile(rf"({NUM})\s*€")  # 15 € → 15 euros
_euro_word = re.compile(r"\s*€")  # a lone €
_pct = re.compile(rf"(-|−)?\s*({NUM}(?:[,.]\d+)?)\s*%")  # -20 % → moins 20 pour cent
_unit = re.compile(rf"({NUM}(?:[,.]\d+)?)\s*({'|'.join(sorted(UNITS, key=len, reverse=True))})\b")
_caliber = re.compile(r"\b(\d{1,3})/(\d{1,3})\b")  # crevettes 30/40 → 30 40 (calibre)
_times = re.compile(r"(\d)\s*[×x]\s*(?=[A-Za-zÀ-ÿ])")  # 2 × Bar → 2 Bar
_hour = re.compile(r"\b(\d{1,2})\s*h\s*(\d{2})?\b")  # 15h30 → 15 heures 30
_lexicon = re.compile(r"(?<!\w)(" + "|".join(re.escape(k) for k in sorted(LEXICON, key=len, reverse=True)) + r")(?!\w)")
_spaces = re.compile(r"[  ]")


def for_speech(text: str) -> str:
    t = _spaces.sub(" ", text)
    t = _money_cents.sub(lambda m: f"{m[1]} euros {m[2] if len(m[2]) == 2 else m[2] + '0'}".replace(" euros 00", " euros"), t)
    t = _money.sub(lambda m: f"{m[1]} euros", t)
    t = _euro_word.sub(" euros", t)
    t = _pct.sub(lambda m: f"{'moins ' if m[1] else ''}{m[2]} pour cent", t)
    t = _unit.sub(lambda m: f"{m[1]} {UNITS[m[2]]}", t)
    t = _caliber.sub(r"\1 \2", t)
    t = _times.sub(r"\1 ", t)
    t = _hour.sub(lambda m: f"{m[1]} heures{' ' + m[2] if m[2] and m[2] != '00' else ''}", t)
    t = _lexicon.sub(lambda m: LEXICON.get(m[1], LEXICON.get(m[1].upper(), m[1])), t)
    return t
