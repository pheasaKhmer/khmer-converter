import sqlite3
import subprocess
import sys

import pytest

from khmer_converter.feedback import MAX_LENGTH, FeedbackStore


@pytest.fixture
def store(tmp_path):
    return FeedbackStore(tmp_path / "feedback.db")


def test_add_and_read(store):
    first = store.add(
        source="web", direction="to_khmer", input="srolanh", output="ស្រលាញ់", correction="ស្រឡាញ់"
    )
    (row,) = store.all()
    assert row.id == first
    assert (row.source, row.direction, row.input, row.output, row.correction) == (
        "web",
        "to_khmer",
        "srolanh",
        "ស្រលាញ់",
        "ស្រឡាញ់",
    )
    assert row.created_at.endswith("+00:00")


def test_only_the_submitted_fields_are_stored(store, tmp_path):
    store.add(source="telegram", direction="to_latin", input="ទេ", output="te", correction="tei")
    columns = [
        c[1]
        for c in sqlite3.connect(tmp_path / "feedback.db").execute("PRAGMA table_info(feedback)")
    ]
    assert columns == ["id", "created_at", "source", "direction", "input", "output", "correction"]


def test_empty_or_long_corrections_are_refused(store):
    with pytest.raises(ValueError, match="empty"):
        store.add(source="web", direction="to_khmer", input="a", output="b", correction="  ")
    with pytest.raises(ValueError, match="limited"):
        store.add(
            source="web",
            direction="to_khmer",
            input="a" * (MAX_LENGTH + 1),
            output="b",
            correction="c",
        )
    assert store.all() == []


def test_unknown_source_is_refused(store):
    with pytest.raises(sqlite3.IntegrityError):
        store.add(source="email", direction="to_khmer", input="a", output="b", correction="c")


def test_corrections_export_as_test_set_rows(store):
    store.add(
        source="web", direction="to_khmer", input="srolanh\toun", output="x", correction="ស្រឡាញ់អូន"
    )
    store.add(source="telegram", direction="to_latin", input="ទេ", output="te", correction="tei")
    assert store.as_testset() == (
        "# romanized\tkhmer\treviewed\nsrolanh oun\tស្រឡាញ់អូន\tno\ntei\tទេ\tno\n"
    )


def test_export_command(store, tmp_path):
    store.add(source="web", direction="to_khmer", input="bong", output="បង់", correction="បង")
    out = subprocess.run(
        [sys.executable, "-m", "khmer_converter.feedback", str(tmp_path / "feedback.db")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout
    assert out.endswith("bong\tបង\tno\n")
