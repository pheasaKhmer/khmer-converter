import re

import pytest
from fastapi.testclient import TestClient
from khmer_engine import Engine

from khmer_converter.feedback import FeedbackStore
from khmer_converter.service import MAX_INPUT, Converter
from khmer_converter.web import create_app


@pytest.fixture(scope="module")
def converter():
    return Converter(Engine())


@pytest.fixture
def store(tmp_path):
    return FeedbackStore(tmp_path / "feedback.db")


@pytest.fixture
def client(converter, store):
    return TestClient(create_app(converter, store))


def test_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "<title>Khmer Converter</title>" in response.text


def test_page_loads_nothing_from_other_sites(client):
    page = client.get("/").text
    assert not re.search(r"""(src|href)=["']?(https?:)?//(?!github\.com/pheasaKhmer)""", page)
    assert not re.search(r"url\((https?:)?//", page)
    policy = client.get("/").headers["content-security-policy"]
    assert "default-src 'self'" in policy


def test_font_is_served(client):
    response = client.get("/static/fonts/kantumruy-pro-khmer.woff2")
    assert response.status_code == 200
    assert response.content[:4] == b"wOF2"


def test_healthz(client):
    assert client.get("/healthz").json() == {"ok": True}


def test_convert_to_khmer(client):
    body = client.post("/api/convert", json={"text": "bong srolanh oun"}).json()
    assert body["direction"] == "to_khmer"
    assert body["text"] == "បងស្រឡាញ់អូន"
    assert body["words"][0] == {
        "typed": "bong",
        "start": 0,
        "end": 4,
        "choices": body["words"][0]["choices"],
    }
    assert body["words"][0]["choices"][:2] == ["បង", "បង់"]


def test_convert_to_latin_in_both_styles(client):
    chat = client.post("/api/convert", json={"text": "សុខសប្បាយទេ"}).json()
    assert (chat["direction"], chat["text"], chat["words"]) == ("to_latin", "soksabay te", [])
    ungegn = client.post("/api/convert", json={"text": "សុខសប្បាយទេ", "style": "ungegn"}).json()
    assert ungegn["text"] == "sŏkhsâbbay té"


def test_direction_can_be_forced(client):
    body = client.post("/api/convert", json={"text": "ok", "direction": "to_latin"}).json()
    assert (body["direction"], body["text"]) == ("to_latin", "ok")


def test_convert_rejects_bad_requests(client):
    assert client.post("/api/convert", json={"text": "a" * (MAX_INPUT + 1)}).status_code == 422
    assert client.post("/api/convert", json={"text": "te", "style": "fancy"}).status_code == 422
    assert client.post("/api/convert", json={}).status_code == 422


def test_feedback_is_stored(client, store):
    report = {"direction": "to_khmer", "input": "srolanh", "output": "ស្រលាញ់", "correction": "ស្រឡាញ់"}
    assert client.post("/api/feedback", json=report).json() == {"ok": True}
    (row,) = store.all()
    assert (row.source, row.input, row.correction) == ("web", "srolanh", "ស្រឡាញ់")


def test_feedback_rejects_empty_corrections(client, store):
    report = {"direction": "to_khmer", "input": "te", "output": "ទេ", "correction": "   "}
    assert client.post("/api/feedback", json=report).status_code == 400
    assert store.all() == []


def test_conversions_are_not_stored(client, store):
    client.post("/api/convert", json={"text": "sok sabay te"})
    assert store.all() == []
