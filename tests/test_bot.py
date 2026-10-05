import asyncio
from types import SimpleNamespace

import pytest
from khmer_engine import Engine
from telegram.error import BadRequest, Conflict, NetworkError
from telegram.ext import Application

from khmer_converter.bot import (
    FEEDBACK_PROMPT,
    FEEDBACK_TIMEOUT,
    Entry,
    KhmerBot,
    RateLimiter,
    RecentConversions,
    keyboard,
    main,
)
from khmer_converter.feedback import FeedbackStore
from khmer_converter.service import Converter


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeMessage:
    """Just enough of telegram.Message for the handlers."""

    next_id = 100

    def __init__(self, text: str = "", chat_id: int = 1, reply_to: "FakeMessage | None" = None):
        FakeMessage.next_id += 1
        self.message_id = FakeMessage.next_id
        self.chat_id = chat_id
        self.text = text
        self.reply_to_message = reply_to
        self.reply_markup = None
        self.replies: list[FakeMessage] = []

    async def reply_text(self, text: str, reply_markup=None) -> "FakeMessage":
        reply = FakeMessage(text, self.chat_id)
        reply.reply_markup = reply_markup
        self.replies.append(reply)
        return reply


class FakeQuery:
    def __init__(self, message: FakeMessage, data: str):
        self.message = message
        self.data = data
        self.answers: list[str | None] = []

    async def answer(self, text: str | None = None) -> None:
        self.answers.append(text)

    async def edit_message_text(self, text: str, reply_markup=None) -> None:
        if text == self.message.text and labels(reply_markup) == labels(self.message.reply_markup):
            raise BadRequest("Message is not modified")  # what Telegram does
        self.message.text = text
        self.message.reply_markup = reply_markup


class FakeInlineQuery:
    def __init__(self, query: str, user_id: int = 7):
        self.query = query
        self.from_user = SimpleNamespace(id=user_id)
        self.results = None

    async def answer(self, results, cache_time: int = 0) -> None:
        self.results = results


def run(coroutine):
    return asyncio.run(coroutine)


@pytest.fixture(scope="module")
def converter():
    return Converter(Engine())


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path):
    return FeedbackStore(tmp_path / "feedback.db")


@pytest.fixture
def bot(converter, store, clock):
    return KhmerBot(
        converter,
        store,
        limiter=RateLimiter(limit=3, window=60, clock=clock),
        recent=RecentConversions(ttl=3600, clock=clock),
        clock=clock,
    )


def context(**user_data):
    return SimpleNamespace(user_data=dict(user_data), args=[])


def send(bot, text, ctx=None, user_id=7, reply_to=None):
    message = FakeMessage(text, reply_to=reply_to)
    update = SimpleNamespace(effective_message=message, effective_user=SimpleNamespace(id=user_id))
    run(bot.message(update, ctx or context()))
    return message.replies[-1]


def tap(bot, reply, data, ctx=None):
    query = FakeQuery(reply, data)
    run(bot.button(SimpleNamespace(callback_query=query), ctx or context()))
    return query


def labels(markup):
    return [[button.text for button in row] for row in markup.inline_keyboard]


def test_romanized_text_gets_khmer_with_buttons_for_ambiguous_words(bot):
    reply = send(bot, "bong luy")
    assert reply.text == "បង់លុយ"
    rows = labels(reply.reply_markup)
    assert rows[0][0] == "✓ បង់"
    assert "បង" in rows[0]
    assert rows[-1] == ["Wrong conversion"]


def test_tapping_a_reading_edits_the_reply(bot):
    reply = send(bot, "bong luy")
    bong = labels(reply.reply_markup)[0].index("បង")
    tap(bot, reply, f"alt:0:{bong}")
    assert reply.text == "បងលុយ"
    assert labels(reply.reply_markup)[0][bong] == "✓ បង"
    tap(bot, reply, "alt:0:0")  # back to the first reading
    assert reply.text == "បង់លុយ"


def test_tapping_the_reading_already_shown_is_harmless(bot):
    reply = send(bot, "bong luy")
    query = tap(bot, reply, "alt:0:0")
    assert reply.text == "បង់លុយ"
    assert query.answers == [None]


def test_khmer_gets_romanized_with_a_style_toggle(bot):
    reply = send(bot, "សុខសប្បាយទេ")
    assert reply.text == "soksabay te"
    assert labels(reply.reply_markup)[0] == ["✓ Chat", "UNGEGN"]
    tap(bot, reply, "sty:ungegn")
    assert reply.text == "sŏkhsâbbay té"
    assert labels(reply.reply_markup)[0] == ["Chat", "✓ UNGEGN"]


def test_style_command(bot):
    ctx = context()
    ctx.args = ["UNGEGN"]
    message = FakeMessage("/style ungegn")
    run(bot.style(SimpleNamespace(effective_message=message), ctx))
    assert ctx.user_data["style"] == "ungegn"
    assert "ungegn" in message.replies[-1].text
    assert send(bot, "ទេ", ctx).text == "té"


def test_wrong_conversion_saves_only_the_reply(bot, store):
    ctx = context()
    reply = send(bot, "srolanh", ctx)
    tap(bot, reply, "wrong", ctx)
    prompt = reply.replies[-1]
    assert prompt.text == FEEDBACK_PROMPT
    thanks = send(bot, "ស្រឡាញ់", ctx, reply_to=prompt)
    assert thanks.text.startswith("Thanks")
    (row,) = store.all()
    assert (row.source, row.direction, row.input, row.output, row.correction) == (
        "telegram",
        "to_khmer",
        "srolanh",
        reply.text,
        "ស្រឡាញ់",
    )
    assert "feedback" not in ctx.user_data


def test_a_late_correction_is_not_saved(bot, store, clock):
    ctx = context()
    reply = send(bot, "srolanh", ctx)
    tap(bot, reply, "wrong", ctx)
    clock.now += FEEDBACK_TIMEOUT + 1
    send(bot, "ស្រឡាញ់", ctx, reply_to=reply.replies[-1])
    assert store.all() == []


def test_ordinary_messages_are_not_saved(bot, store):
    send(bot, "sok sabay te")
    assert store.all() == []


def test_old_conversions_cannot_be_changed(bot, clock):
    reply = send(bot, "bong luy")
    clock.now += 3601
    query = tap(bot, reply, "alt:0:1")
    assert reply.text == "បង់លុយ"
    assert "too old" in query.answers[0]


def test_rate_limit(bot, clock):
    for _ in range(3):
        send(bot, "te")
    assert "minute" in send(bot, "te").text
    assert send(bot, "te", user_id=8).text == "ទេ"  # other users are not affected
    clock.now += 61
    assert send(bot, "te").text == "ទេ"


def test_text_without_khmer_or_letters(bot):
    assert send(bot, "123").text == "123"
    assert "Send me Khmer" in send(bot, "   ").text


def test_inline_query_offers_the_best_readings(bot):
    query = FakeInlineQuery("sok sabay te")
    run(bot.inline(SimpleNamespace(inline_query=query), context()))
    titles = [r.title for r in query.results]
    assert titles[0] == "សុខសប្បាយទេ"
    assert len(titles) == len(set(titles)) <= 3
    assert query.results[0].input_message_content.message_text == "សុខសប្បាយទេ"


def test_inline_query_romanizes_khmer_in_both_styles(bot):
    query = FakeInlineQuery("សុខសប្បាយទេ")
    run(bot.inline(SimpleNamespace(inline_query=query), context()))
    assert [r.title for r in query.results] == ["soksabay te", "sŏkhsâbbay té"]


def test_empty_inline_query(bot):
    query = FakeInlineQuery("  ")
    run(bot.inline(SimpleNamespace(inline_query=query), context()))
    assert query.results == []


def test_keyboard_limits_rows_and_buttons(converter):
    result = converter.convert("te te te te te te")
    rows = labels(keyboard(Entry(result, "chat")))
    assert len(rows) <= 5
    assert all(len(row) <= 4 for row in rows)


def test_recent_conversions_drop_the_oldest(converter, clock):
    recent = RecentConversions(size=2, ttl=3600, clock=clock)
    entry = Entry(converter.convert("te"), "chat")
    for message_id in (1, 2, 3):
        recent.put((1, message_id), entry)
    assert recent.get((1, 1)) is None
    assert recent.get((1, 3)) is entry


def test_rate_limiter_window(clock):
    limiter = RateLimiter(limit=2, window=10, clock=clock)
    assert limiter.allow(1) and limiter.allow(1)
    assert not limiter.allow(1)
    clock.now += 10
    assert limiter.allow(1)


def test_application_has_every_handler(bot):
    app = bot.application("123456:TEST-TOKEN")
    assert isinstance(app, Application)
    assert len(app.handlers[0]) == 5


def test_network_errors_are_logged_in_one_line(bot, caplog):
    run(bot.error(None, SimpleNamespace(error=NetworkError("httpx.ReadError: "))))
    (record,) = caplog.records
    assert record.levelname == "WARNING"
    assert record.exc_info is None
    run(bot.error(None, SimpleNamespace(error=Conflict("terminated by other getUpdates request"))))
    assert caplog.records[-1].levelname == "WARNING"
    assert "another copy" in caplog.records[-1].getMessage()
    run(bot.error(None, SimpleNamespace(error=ValueError("bug"))))
    assert caplog.records[-1].levelname == "ERROR"
    assert caplog.records[-1].exc_info is not None


def test_main_needs_a_token(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="TELEGRAM_BOT_TOKEN"):
        main()
