import re
import unicodedata

_UPPER = str.maketrans({"i": "İ", "ı": "I"})
_LOWER = str.maketrans({"I": "ı", "İ": "i"})


def tr_upper(text: str) -> str:
    return text.translate(_UPPER).upper()


def tr_lower(text: str) -> str:
    return text.translate(_LOWER).lower()


def is_upper_word(word: str) -> bool:
    letters = [c for c in word if c.isalpha()]
    return bool(letters) and tr_upper(word) == word


def ascii_fold(text: str) -> str:
    text = tr_lower(text).replace("ı", "i")
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


def name_key(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", ascii_fold(text)))


def squash(text: str) -> str:
    return " ".join(str(text or "").replace("\xa0", " ").split())
