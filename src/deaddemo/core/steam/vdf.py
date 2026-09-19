"""Minimal parser for Valve's KeyValues (VDF) text format.

Handles the subset Steam's config files use: quoted keys and values, nested ``{}`` blocks,
``//`` line comments, and backslash escapes inside quoted strings. Duplicate keys keep the last
value.
"""

from __future__ import annotations

from typing import Any

_ESCAPES = {"n": "\n", "t": "\t", "\\": "\\", '"': '"'}


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif c == "/" and text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end == -1 else end + 1
        elif c in "{}":
            tokens.append(c)
            i += 1
        elif c == '"':
            i += 1
            buf: list[str] = []
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n:
                    buf.append(_ESCAPES.get(text[i + 1], text[i + 1]))
                    i += 2
                else:
                    buf.append(text[i])
                    i += 1
            i += 1  # closing quote
            tokens.append('"' + "".join(buf))  # keep a marker so "{" as a value is unambiguous
        else:
            start = i
            while i < n and not text[i].isspace() and text[i] not in "{}":
                i += 1
            tokens.append('"' + text[start:i])
    return tokens


def parse_vdf(text: str) -> dict[str, Any]:
    tokens = _tokenize(text.lstrip("﻿"))
    pos = 0

    def parse_block() -> dict[str, Any]:
        nonlocal pos
        result: dict[str, Any] = {}
        while pos < len(tokens):
            tok = tokens[pos]
            if tok == "}":
                pos += 1
                return result
            if tok == "{":
                raise ValueError("unexpected '{' in VDF")
            key = tok[1:]
            pos += 1
            if pos >= len(tokens):
                raise ValueError(f"missing value for key {key!r}")
            nxt = tokens[pos]
            if nxt == "{":
                pos += 1
                result[key] = parse_block()
            else:
                result[key] = nxt[1:]
                pos += 1
        return result

    return parse_block()
