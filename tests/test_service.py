import pytest
from khmer_engine import Engine

from khmer_converter.service import MAX_INPUT, Converter, direction_of, with_choices


@pytest.fixture(scope="module")
def converter():
    return Converter(Engine())


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("sok sabay te", "to_khmer"),
        ("សុខសប្បាយទេ", "to_latin"),
        ("ខ្ញុំ love អូន", "to_latin"),  # mostly Khmer
        ("okay bong បង", "to_khmer"),  # mostly Latin
        ("123 ?", "to_khmer"),
        ("", "to_khmer"),
    ],
)
def test_direction(text, expected):
    assert direction_of(text) == expected


def test_to_khmer_gives_words_with_alternatives(converter):
    result = converter.convert("bong srolanh oun")
    assert result.direction == "to_khmer"
    assert result.text == "បងស្រលាញ់អូន"
    assert [(w.typed, w.start, w.end) for w in result.words] == [
        ("bong", 0, 4),
        ("srolanh", 5, 12),
        ("oun", 13, 16),
    ]
    assert result.words[0].choices[:2] == ["បង", "បង់"]


def test_to_latin_in_both_styles(converter):
    assert converter.convert("សុខសប្បាយទេ").text == "soksabay te"
    assert converter.convert("សុខសប្បាយទេ", style="ungegn").text == "sŏkhsâbbay té"
    assert converter.convert("សុខសប្បាយទេ").words == []


def test_long_input_is_cut(converter):
    result = converter.to_khmer("te " * MAX_INPUT)
    assert len(result.input) == MAX_INPUT


def test_with_choices_swaps_one_word(converter):
    result = converter.convert("bong srolanh oun")
    assert with_choices(result, {1: "ស្រឡាញ់"}) == "បងស្រឡាញ់អូន"
    assert with_choices(result, {0: "បង់", 2: "អន"}) == "បង់ស្រលាញ់អន"
    assert with_choices(result, {}) == result.text


def test_with_choices_keeps_english_and_punctuation(converter):
    result = converter.convert("ot mean wifi te?")
    assert result.text == "អត់មាន wifi ទេ?"
    last = len(result.words) - 1
    assert with_choices(result, {last: "តែ"}) == "អត់មាន wifi តែ?"


def test_alternatives_for_the_whole_text(converter):
    result = converter.convert("bong luy")
    assert result.alternatives[0] == result.text
    assert len(result.alternatives) > 1
    assert converter.convert("ទេ").alternatives == ["te"]
