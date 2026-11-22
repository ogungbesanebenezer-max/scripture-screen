"""Spot spoken requests to change Bible translation."""
import re

# Longest names are tried first, so "new king james" never reads as "king james".
SPOKEN_NAMES = {
    "NKJV": ["new king james", "nkjv", "n k j v"],
    "KJV": ["king james", "kjv", "k j v", "authorized version", "authorised version"],
    "AMP": ["amplified"],
    "NIV": ["new international", "niv", "n i v"],
    "ESV": ["english standard", "esv"],
    "NLT": ["new living", "nlt"],
    "NASB": ["new american standard", "nasb"],
    "RSV": ["revised standard", "rsv"],
    "GNT": ["good news bible", "good news translation"],
    "MSG": ["message bible", "message translation", "message version"],
    "ASV": ["american standard", "asv"],
    "BBE": ["basic english", "bbe"],
    "YLT": ["young's literal", "youngs literal", "young literal", "ylt"],
    "YOR": ["yoruba", "yorba", "bibeli mimo"],
    "BSB": ["berean standard", "berean bible", "bsb", "b s b"],
    "WEB": ["world english"],  # never the bare word "web": "you can find it on the web"
}
_NAME_TO_CODE = {name: code for code, names in SPOKEN_NAMES.items() for name in names}
_PATTERN = re.compile(r"\b(" + "|".join(sorted(map(re.escape, _NAME_TO_CODE), key=len, reverse=True)) + r")\b")
_BEFORE = re.compile(r"\b(read|reads|reading|look|see|give|gives|in|from|switch|change|use|using|open|try|turn|go|put)\b")
_AFTER = re.compile(r"\s*(version|translation|bible|says|reads|puts|renders|has it)\b")


def requested_translation(text):
    """Return the translation code the speaker asked for, or None. The last request wins."""
    text = re.sub(r"[^a-z' ]+", " ", text.lower().replace("’", "'"))
    text = re.sub(r" +", " ", text)
    result = None
    for match in _PATTERN.finditer(text):
        before = " ".join(text[: match.start()].split()[-4:])
        if _BEFORE.search(before) or _AFTER.match(text[match.end():]):
            result = _NAME_TO_CODE[match[1]]
    return result
