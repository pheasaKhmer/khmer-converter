"""The Telegram bot.

    TELEGRAM_BOT_TOKEN=... python -m khmer_converter.bot

Send romanized Khmer and it replies in Khmer script, with buttons to swap ambiguous words
for another reading; send Khmer script and it replies romanized. In any chat, type
@botname followed by romanized Khmer to insert the Khmer. /style switches romanization
between chat spelling and UNGEGN.

Environment:
    TELEGRAM_BOT_TOKEN  the bot token from @BotFather (required)
    KHMER_ENGINE_DATA   lexicon directory for the engine (default: its bundled sample)
    FEEDBACK_DB         SQLite file for wrong-conversion reports (default: feedback.db)

Privacy: messages are not logged or written anywhere. The last conversions are kept in
memory for an hour so their buttons work, and user ids are kept in memory for rate
limiting. Only text sent in reply to "Wrong conversion" is saved, without any user id.
"""

import logging
import os
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from telegram import (
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Update,
)
from telegram.error import BadRequest, Conflict, NetworkError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    InlineQueryHandler,
    MessageHandler,
    filters,
)

from khmer_converter.feedback import FeedbackStore
from khmer_converter.service import Converter, Result, Style, with_choices

log = logging.getLogger("khmer_converter.bot")

HELP = (
    "Send me Khmer typed in Latin letters, like “sok sabay te”, and I reply in Khmer script. "
    "Tap a button under my reply to swap a word for another reading.\n\n"
    "Send me Khmer script and I reply in Latin letters. /style switches between chat "
    "spelling and the UNGEGN standard.\n\n"
    "In any chat, type @{bot} sok sabay to insert the Khmer.\n\n"
    "I don't keep your messages. If a conversion is wrong, tap “Wrong conversion” and reply "
    "with the right text: only that is saved, with no name or id, to make me better."
)
FEEDBACK_PROMPT = (
    "Reply to this message with the correct text. Only what you typed, what I gave and "
    "your correction are saved, with no name or id."
)
STYLES: dict[str, Style] = {"chat": "chat", "ungegn": "ungegn"}
MAX_WORD_ROWS = 4  # words with alternatives shown as button rows
MAX_CHOICES = 4  # buttons per word
FEEDBACK_TIMEOUT = 10 * 60  # seconds to reply with a correction


class RateLimiter:
    """At most `limit` events per user in any `window` seconds. Memory only."""

    def __init__(
        self, limit: int = 20, window: float = 60.0, clock: Callable[[], float] = time.monotonic
    ):
        self.limit = limit
        self.window = window
        self.clock = clock
        self._events: dict[int, deque[float]] = {}

    def allow(self, user_id: int) -> bool:
        now = self.clock()
        events = self._events.setdefault(user_id, deque())
        while events and now - events[0] >= self.window:
            events.popleft()
        if len(events) >= self.limit:
            return False
        events.append(now)
        return True


@dataclass
class Entry:
    """A conversion the bot replied with, kept so its buttons can change it."""

    result: Result
    style: Style
    chosen: dict[int, str] = field(default_factory=dict)
    created: float = 0.0

    @property
    def text(self) -> str:
        if self.result.direction == "to_khmer":
            return with_choices(self.result, self.chosen)
        return self.result.text


class RecentConversions:
    """The last conversions by (chat id, message id), dropped after `ttl` seconds or when
    more than `size` are kept. Memory only."""

    def __init__(
        self, size: int = 2000, ttl: float = 3600.0, clock: Callable[[], float] = time.monotonic
    ):
        self.size = size
        self.ttl = ttl
        self.clock = clock
        self._entries: OrderedDict[tuple[int, int], Entry] = OrderedDict()

    def put(self, key: tuple[int, int], entry: Entry) -> None:
        entry.created = self.clock()
        self._entries[key] = entry
        self._entries.move_to_end(key)
        while len(self._entries) > self.size:
            self._entries.popitem(last=False)

    def get(self, key: tuple[int, int]) -> Entry | None:
        entry = self._entries.get(key)
        if entry is None or self.clock() - entry.created > self.ttl:
            self._entries.pop(key, None)
            return None
        return entry


def keyboard(entry: Entry) -> InlineKeyboardMarkup:
    """Buttons under a reply: readings of ambiguous words, or the romanization style."""
    rows: list[list[InlineKeyboardButton]] = []
    if entry.result.direction == "to_khmer":
        ambiguous = [(i, w) for i, w in enumerate(entry.result.words) if len(w.choices) > 1]
        for i, word in ambiguous[:MAX_WORD_ROWS]:
            current = entry.chosen.get(i, word.choices[0])
            rows.append(
                [
                    InlineKeyboardButton(
                        f"✓ {choice}" if choice == current else choice, callback_data=f"alt:{i}:{j}"
                    )
                    for j, choice in enumerate(word.choices[:MAX_CHOICES])
                ]
            )
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    f"✓ {label}" if entry.style == style else label, callback_data=f"sty:{style}"
                )
                for style, label in (("chat", "Chat"), ("ungegn", "UNGEGN"))
            ]
        )
    rows.append([InlineKeyboardButton("Wrong conversion", callback_data="wrong")])
    return InlineKeyboardMarkup(rows)


class KhmerBot:
    def __init__(
        self,
        converter: Converter,
        store: FeedbackStore,
        limiter: RateLimiter | None = None,
        recent: RecentConversions | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.converter = converter
        self.store = store
        self.limiter = limiter or RateLimiter()
        self.recent = recent or RecentConversions()
        self.clock = clock

    def application(self, token: str) -> Application:
        app = Application.builder().token(token).build()
        app.add_handler(CommandHandler(["start", "help"], self.help))
        app.add_handler(CommandHandler("style", self.style))
        app.add_handler(CallbackQueryHandler(self.button))
        app.add_handler(InlineQueryHandler(self.inline))
        private_text = filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE
        app.add_handler(MessageHandler(private_text, self.message))
        app.add_error_handler(self.error)
        return app

    async def error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """A dropped connection while polling is retried by the library, so it gets one
        line; without a handler the library logs a full traceback each time. So does a
        conflict, which means another copy of the bot is running with the same token.
        Anything else is a bug and keeps its traceback."""
        error = context.error
        if isinstance(error, Conflict):
            log.warning("another copy of this bot is running with the same token; stop it")
        elif isinstance(error, NetworkError):
            log.warning("network error, retrying: %s", error)
        else:
            log.error("unhandled error", exc_info=error)

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        name = context.bot.username if getattr(context, "bot", None) else "bot"
        await update.effective_message.reply_text(HELP.format(bot=name))

    async def style(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_data: dict[str, Any] = context.user_data
        if context.args and context.args[0].lower() in STYLES:
            user_data["style"] = STYLES[context.args[0].lower()]
        current = user_data.get("style", "chat")
        await update.effective_message.reply_text(
            f"Romanization style: {current}. Use /style chat or /style ungegn to change it."
        )

    async def message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        user_data: dict[str, Any] = context.user_data
        if await self._take_correction(message, user_data):
            return
        if not self.limiter.allow(update.effective_user.id):
            await message.reply_text("That's a lot of messages. Try again in a minute.")
            return
        style = user_data.get("style", "chat")
        result = self.converter.convert(message.text, style=style)
        if not result.text.strip():
            await message.reply_text("Send me Khmer in Latin letters or in Khmer script.")
            return
        entry = Entry(result, style)
        sent = await message.reply_text(entry.text, reply_markup=keyboard(entry))
        self.recent.put((sent.chat_id, sent.message_id), entry)

    async def _take_correction(self, message: Any, user_data: dict[str, Any]) -> bool:
        """Save the message as a correction if it replies to the feedback prompt."""
        pending = user_data.get("feedback")
        reply_to = message.reply_to_message
        if not pending or reply_to is None or reply_to.message_id != pending["prompt"]:
            return False
        del user_data["feedback"]
        if self.clock() - pending["at"] > FEEDBACK_TIMEOUT:
            await message.reply_text("That was a while ago. Tap “Wrong conversion” again.")
            return True
        try:
            self.store.add(
                source="telegram",
                direction=pending["direction"],
                input=pending["input"],
                output=pending["output"],
                correction=message.text,
            )
        except ValueError as error:
            await message.reply_text(f"I couldn't save that: {error}.")
            return True
        await message.reply_text("Thanks, saved. It will help fix this.")
        return True

    async def button(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        entry = self.recent.get((query.message.chat_id, query.message.message_id))
        if entry is None:
            await query.answer("This one is too old to change. Send it again.")
            return
        data = query.data or ""
        if data == "wrong":
            prompt = await query.message.reply_text(
                FEEDBACK_PROMPT, reply_markup=ForceReply(selective=True)
            )
            context.user_data["feedback"] = {
                "prompt": prompt.message_id,
                "at": self.clock(),
                "direction": entry.result.direction,
                "input": entry.result.input,
                "output": entry.text,
            }
            await query.answer()
            return
        before = entry.text
        if data.startswith("alt:"):
            i, j = (int(part) for part in data.split(":")[1:])
            if i >= len(entry.result.words) or j >= len(entry.result.words[i].choices):
                await query.answer()
                return
            choices = entry.result.words[i].choices
            if j == 0:
                entry.chosen.pop(i, None)
            else:
                entry.chosen[i] = choices[j]
        elif data.startswith("sty:") and data[4:] in STYLES:
            entry.style = STYLES[data[4:]]
            entry.result = self.converter.to_latin(entry.result.input, entry.style)
        await query.answer()
        try:
            await query.edit_message_text(entry.text, reply_markup=keyboard(entry))
        except BadRequest as error:  # tapping the reading already shown changes nothing
            if "not modified" not in str(error).lower() or entry.text != before:
                raise

    async def inline(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.inline_query
        text = query.query.strip()
        if not text or not self.limiter.allow(query.from_user.id):
            await query.answer([], cache_time=5)
            return
        result = self.converter.convert(text)
        if result.direction == "to_khmer":
            options = result.alternatives[:3]
        else:
            options = [result.text, self.converter.to_latin(text, "ungegn").text]
        articles = [
            InlineQueryResultArticle(
                id=str(i),
                title=option,
                description=text,
                input_message_content=InputTextMessageContent(option),
            )
            for i, option in enumerate(dict.fromkeys(o for o in options if o.strip()))
        ]
        await query.answer(articles, cache_time=30)


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN to the token @BotFather gave you.")
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
    )
    # httpx logs every request URL at INFO, and Telegram API URLs contain the token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    store = FeedbackStore(os.environ.get("FEEDBACK_DB", "feedback.db"))
    bot = KhmerBot(Converter(), store)
    log.info("starting")
    bot.application(token).run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
