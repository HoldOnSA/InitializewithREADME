"""A minimal Borsh codec, stdlib only.

Just enough of Borsh to read what this program writes: fixed-width integers,
bools, 32-byte pubkeys, strings and vecs of nested structs. Keeping it
dependency-free means the decoding tests run anywhere Python does, with no
network and no pip install.

Layout format - a list of `(field_name, type)` pairs, where type is one of:

    "u8" "u16" "u32" "u64" "u128" "i8" "i16" "i32" "i64" "i128"
    "bool" "pubkey" "string" "bytes32"
    ("vec", <type>)
    ("array", <type>, <length>)
    ("struct", <layout>)
    ("option", <type>)
"""

from __future__ import annotations

import struct
from typing import Any, Dict, List, Sequence, Tuple

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

_INT_FORMATS = {
    "u8": ("<B", 1),
    "u16": ("<H", 2),
    "u32": ("<I", 4),
    "u64": ("<Q", 8),
    "i8": ("<b", 1),
    "i16": ("<h", 2),
    "i32": ("<i", 4),
    "i64": ("<q", 8),
}


class BorshError(ValueError):
    pass


def b58encode(raw: bytes) -> str:
    """Base58 (Bitcoin alphabet), as Solana renders pubkeys."""
    n = int.from_bytes(raw, "big")
    out = ""
    while n > 0:
        n, rem = divmod(n, 58)
        out = _B58_ALPHABET[rem] + out
    # Leading zero bytes become leading '1's.
    for byte in raw:
        if byte != 0:
            break
        out = "1" + out
    return out or "1"


def b58decode(text: str) -> bytes:
    n = 0
    for char in text:
        index = _B58_ALPHABET.find(char)
        if index < 0:
            raise BorshError(f"invalid base58 character {char!r}")
        n = n * 58 + index
    leading = len(text) - len(text.lstrip("1"))
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\x00" * leading + body


class Reader:
    """A cursor over a borsh buffer."""

    def __init__(self, data: bytes, offset: int = 0):
        self.data = data
        self.offset = offset

    def _take(self, n: int) -> bytes:
        if self.offset + n > len(self.data):
            raise BorshError(
                f"buffer underrun: wanted {n} bytes at {self.offset}, have {len(self.data)}"
            )
        chunk = self.data[self.offset : self.offset + n]
        self.offset += n
        return chunk

    def read(self, kind) -> Any:
        if isinstance(kind, str):
            if kind in _INT_FORMATS:
                fmt, size = _INT_FORMATS[kind]
                return struct.unpack(fmt, self._take(size))[0]
            if kind == "u128":
                return int.from_bytes(self._take(16), "little", signed=False)
            if kind == "i128":
                return int.from_bytes(self._take(16), "little", signed=True)
            if kind == "bool":
                return self._take(1)[0] != 0
            if kind == "pubkey":
                return b58encode(self._take(32))
            if kind == "bytes32":
                return self._take(32)
            if kind == "string":
                length = struct.unpack("<I", self._take(4))[0]
                return self._take(length).decode("utf-8")
            raise BorshError(f"unknown scalar type {kind!r}")

        tag = kind[0]
        if tag == "vec":
            length = struct.unpack("<I", self._take(4))[0]
            return [self.read(kind[1]) for _ in range(length)]
        if tag == "array":
            return [self.read(kind[1]) for _ in range(kind[2])]
        if tag == "struct":
            return self.read_struct(kind[1])
        if tag == "option":
            return self.read(kind[1]) if self._take(1)[0] else None
        raise BorshError(f"unknown compound type {tag!r}")

    def read_struct(self, layout: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
        return {name: self.read(kind) for name, kind in layout}


class Writer:
    """Encodes the same layouts. Used by the tests to round-trip."""

    def __init__(self):
        self.parts: List[bytes] = []

    def write(self, kind, value) -> "Writer":
        if isinstance(kind, str):
            if kind in _INT_FORMATS:
                fmt, _ = _INT_FORMATS[kind]
                self.parts.append(struct.pack(fmt, value))
            elif kind == "u128":
                self.parts.append(int(value).to_bytes(16, "little", signed=False))
            elif kind == "i128":
                self.parts.append(int(value).to_bytes(16, "little", signed=True))
            elif kind == "bool":
                self.parts.append(b"\x01" if value else b"\x00")
            elif kind == "pubkey":
                raw = value if isinstance(value, bytes) else b58decode(value)
                if len(raw) != 32:
                    raise BorshError(f"pubkey must be 32 bytes, got {len(raw)}")
                self.parts.append(raw)
            elif kind == "bytes32":
                self.parts.append(bytes(value).rjust(32, b"\x00")[:32])
            elif kind == "string":
                encoded = value.encode("utf-8")
                self.parts.append(struct.pack("<I", len(encoded)) + encoded)
            else:
                raise BorshError(f"unknown scalar type {kind!r}")
            return self

        tag = kind[0]
        if tag == "vec":
            self.parts.append(struct.pack("<I", len(value)))
            for item in value:
                self.write(kind[1], item)
        elif tag == "array":
            for item in value:
                self.write(kind[1], item)
        elif tag == "struct":
            self.write_struct(kind[1], value)
        elif tag == "option":
            if value is None:
                self.parts.append(b"\x00")
            else:
                self.parts.append(b"\x01")
                self.write(kind[1], value)
        else:
            raise BorshError(f"unknown compound type {tag!r}")
        return self

    def write_struct(self, layout: Sequence[Tuple[str, Any]], value: Dict[str, Any]) -> "Writer":
        for name, kind in layout:
            if name not in value:
                raise BorshError(f"missing field {name!r}")
            self.write(kind, value[name])
        return self

    def bytes(self) -> bytes:
        return b"".join(self.parts)


def decode_struct(layout: Sequence[Tuple[str, Any]], data: bytes, offset: int = 0) -> Dict[str, Any]:
    return Reader(data, offset).read_struct(layout)


def encode_struct(layout: Sequence[Tuple[str, Any]], value: Dict[str, Any]) -> bytes:
    return Writer().write_struct(layout, value).bytes()
