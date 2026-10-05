"""Conversions people report as wrong, with the correction they give.

Only what a person submits through a "wrong conversion" button is stored: the input,
the output they saw, and their correction, with the time, where it came from (web or
telegram) and the direction. No user names, ids or addresses are kept.

    python -m khmer_converter.feedback feedback.db       # print corrections as TSV

The TSV has the columns of khmer-engine's eval/testset.tsv (romanized, khmer, reviewed),
so corrections can be checked and added to the engine's test set.
"""

import sqlite3
import sys
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

MAX_LENGTH = 1000  # characters per field

SCHEMA = """
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('web', 'telegram')),
    direction TEXT NOT NULL CHECK (direction IN ('to_khmer', 'to_latin')),
    input TEXT NOT NULL,
    output TEXT NOT NULL,
    correction TEXT NOT NULL
)
"""


@dataclass(frozen=True)
class Feedback:
    id: int
    created_at: str
    source: str
    direction: str
    input: str
    output: str
    correction: str


class FeedbackStore:
    def __init__(self, path: Path | str):
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.execute(SCHEMA)

    def add(self, *, source: str, direction: str, input: str, output: str, correction: str) -> int:
        """Store one report and return its id. Empty corrections are refused."""
        fields = [input.strip(), output.strip(), correction.strip()]
        if not fields[2]:
            raise ValueError("the correction is empty")
        if any(len(f) > MAX_LENGTH for f in fields):
            raise ValueError(f"each field is limited to {MAX_LENGTH} characters")
        created = datetime.now(UTC).isoformat(timespec="seconds")
        with self._lock, self._db:
            cursor = self._db.execute(
                "INSERT INTO feedback (created_at, source, direction, input, output, correction)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (created, source, direction, *fields),
            )
        return int(cursor.lastrowid or 0)

    def all(self) -> list[Feedback]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, created_at, source, direction, input, output, correction"
                " FROM feedback ORDER BY id"
            ).fetchall()
        return [Feedback(*row) for row in rows]

    def as_testset(self) -> str:
        """Corrections as rows for khmer-engine's eval/testset.tsv, all unreviewed."""
        lines = ["# romanized\tkhmer\treviewed"]
        for f in self.all():
            romanized, khmer = (f.input, f.correction)
            if f.direction == "to_latin":
                romanized, khmer = f.correction, f.input
            lines.append(f"{_one_line(romanized)}\t{_one_line(khmer)}\tno")
        return "\n".join(lines) + "\n"


def _one_line(text: str) -> str:
    return " ".join(text.split())


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m khmer_converter.feedback FEEDBACK.db")
    sys.stdout.write(FeedbackStore(sys.argv[1]).as_testset())
