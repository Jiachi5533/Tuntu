from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit


_HEX_BTIH = re.compile(r"^[0-9a-fA-F]{40}$")
_BASE32_BTIH = re.compile(r"^[A-Z2-7a-z]{32}$")


class InvalidMagnet(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Magnet:
    btih: str
    parameters: tuple[tuple[str, str], ...] = ()

    @property
    def canonical_uri(self) -> str:
        canonical = f"magnet:?xt=urn:btih:{self.btih}"
        if not self.parameters:
            return canonical
        return f"{canonical}&{urlencode(self.parameters, doseq=True)}"


def normalize_btih(value: str) -> str:
    candidate = value.strip()
    if _HEX_BTIH.fullmatch(candidate):
        return candidate.casefold()
    if not _BASE32_BTIH.fullmatch(candidate):
        raise InvalidMagnet("btih must be 40 hexadecimal or 32 Base32 characters")
    try:
        decoded = base64.b32decode(candidate.upper(), casefold=True)
    except binascii.Error as exc:
        raise InvalidMagnet("invalid Base32 btih") from exc
    if len(decoded) != 20:
        raise InvalidMagnet("btih must decode to 20 bytes")
    return decoded.hex()


def parse_magnet(uri: str) -> Magnet:
    try:
        parsed = urlsplit(uri)
    except ValueError as exc:
        raise InvalidMagnet("invalid magnet URI") from exc
    if parsed.scheme.casefold() != "magnet":
        raise InvalidMagnet("URI scheme must be magnet")

    raw_btih_values = []
    parameters = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.casefold() != "xt":
            parameters.append((key, value))
            continue
        prefix, separator, hash_value = value.partition(":")
        if prefix.casefold() != "urn" or not separator:
            parameters.append((key, value))
            continue
        hash_kind, separator, hash_value = hash_value.partition(":")
        if hash_kind.casefold() == "btih" and separator:
            raw_btih_values.append(hash_value)
        else:
            parameters.append((key, value))

    if not raw_btih_values:
        raise InvalidMagnet("magnet does not contain a v1 btih")
    normalized = {normalize_btih(value) for value in raw_btih_values}
    if len(normalized) != 1:
        raise InvalidMagnet("magnet contains conflicting btih values")
    return Magnet(btih=normalized.pop(), parameters=tuple(parameters))
