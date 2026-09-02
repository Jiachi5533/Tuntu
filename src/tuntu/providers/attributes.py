from __future__ import annotations

import re
import unicodedata

from tuntu.models import TruthValue


_FC2_CODE = re.compile(r"^FC2[\s_-]*PPV[\s_-]*([0-9]{5,8})$", re.IGNORECASE)
_STANDARD_CODE = re.compile(r"^([A-Z][A-Z0-9]{1,9})[\s_-]*0*([0-9]{1,6})$", re.IGNORECASE)
_SIZE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*([KMGT]I?B)\b", re.IGNORECASE)


def normalize_jav_identity(
    raw_key: str, *, fallback_namespace: str = "jav"
) -> tuple[str, str]:
    cleaned = " ".join(unicodedata.normalize("NFKC", raw_key).strip().split())
    fc2 = _FC2_CODE.fullmatch(cleaned)
    if fc2:
        return ("jav", f"FC2-PPV-{fc2.group(1)}")
    standard = _STANDARD_CODE.fullmatch(cleaned)
    if standard:
        prefix, number = standard.groups()
        return ("jav", f"{prefix.upper()}-{str(int(number)).zfill(3)}")
    return (fallback_namespace.casefold(), cleaned.casefold())


def parse_size_mb(text: str) -> float | None:
    match = _SIZE.search(text)
    if match is None:
        return None
    value = float(match.group(1))
    unit = match.group(2).upper()
    powers = {"KB": -1, "KIB": -1, "MB": 0, "MIB": 0, "GB": 1, "GIB": 1, "TB": 2, "TIB": 2}
    result = value * (1024 ** powers[unit])
    return round(result, 3)


def classify_attributes(text: str) -> tuple[TruthValue, TruthValue, TruthValue]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    chinese = (
        TruthValue.YES
        if any(marker in normalized for marker in ("字幕", "中文", "chs", "cht"))
        else TruthValue.UNKNOWN
    )
    if any(marker in normalized for marker in ("uncensored", "无码", "無修正")):
        uncensored = TruthValue.YES
    elif any(marker in normalized for marker in ("censored", "有码")):
        uncensored = TruthValue.NO
    else:
        uncensored = TruthValue.UNKNOWN
    uhd = (
        TruthValue.YES
        if any(marker in normalized for marker in ("2160p", "4k", "uhd"))
        else TruthValue.UNKNOWN
    )
    return chinese, uncensored, uhd
