"""What both front ends need from the engine: conversion in either direction, with
alternatives for each word, and swapping in an alternative afterwards."""

import re
from dataclasses import dataclass
from typing import Literal

import khmer_engine
from khmer_engine import Engine

Direction = Literal["to_khmer", "to_latin"]
Style = Literal["chat", "ungegn"]

MAX_INPUT = 2000  # characters; longer input is cut, to keep each request cheap

_KHMER_LETTER = re.compile("[\\u1780-\\u17dd]")
_LATIN_LETTER = re.compile("[A-Za-z]")


def direction_of(text: str) -> Direction:
    """Khmer script goes to Latin letters, anything else to Khmer script."""
    khmer = len(_KHMER_LETTER.findall(text))
    latin = len(_LATIN_LETTER.findall(text))
    return "to_latin" if khmer and khmer >= latin else "to_khmer"


@dataclass(frozen=True)
class Word:
    """A converted word: what was typed, where, and its readings, best first."""

    typed: str
    start: int
    end: int
    choices: list[str]


@dataclass(frozen=True)
class Result:
    direction: Direction
    input: str
    text: str
    words: list[Word]  # only for to_khmer; empty when romanizing


class Converter:
    def __init__(self, engine: Engine | None = None):
        self.engine = engine or khmer_engine.default_engine()

    def to_khmer(self, text: str, alternatives: int = 5) -> Result:
        text = text[:MAX_INPUT]
        conversion = self.engine.analyze(text, alternatives)
        words = [
            Word(t.typed, t.start, t.end, list(dict.fromkeys(c.text for c in t.choices)))
            for t in conversion.tokens
        ]
        return Result("to_khmer", text, conversion.text, words)

    def to_latin(self, text: str, style: Style = "chat") -> Result:
        text = text[:MAX_INPUT]
        return Result("to_latin", text, self.engine.romanize(text, style), [])

    def convert(self, text: str, style: Style = "chat") -> Result:
        """Convert in whichever direction the text calls for."""
        if direction_of(text) == "to_latin":
            return self.to_latin(text, style)
        return self.to_khmer(text)


def with_choices(result: Result, chosen: dict[int, str]) -> str:
    """The converted text with word `i` replaced by `chosen[i]` for each entry.

    Converted words appear in the output in the same order as in `result.words`, so the
    output is walked word by word and each chosen word is put in place of the original.
    """
    out = []
    position = 0
    for i, word in enumerate(result.words):
        original = word.choices[0]
        found = result.text.find(original, position)
        if found < 0:
            continue
        out.append(result.text[position:found])
        out.append(chosen.get(i, original))
        position = found + len(original)
    out.append(result.text[position:])
    return "".join(out)
