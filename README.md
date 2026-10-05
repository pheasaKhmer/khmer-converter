# khmer-converter

A web page and a Telegram bot for converting between romanized Khmer and Khmer script, built
on [khmer-engine](https://github.com/pheasaKhmer/khmer-engine).

- **Web page**: two text boxes that convert as you type, in either direction. Tap a converted
  word to pick another reading, copy the result, and switch romanization between chat
  spelling and the UNGEGN standard.
- **Telegram bot**: send `sok sabay te` and get សុខសប្បាយទេ, with buttons to swap ambiguous
  words. Send Khmer script and get it romanized. In any chat, `@yourbot sok sabay` inserts
  the Khmer.

## Privacy

Nothing people type is stored or logged. The page loads nothing from other sites, not even
its font, and the server keeps no access log in Docker. The bot keeps its last conversions in
memory for an hour so their buttons work, and keeps user ids in memory for rate limiting.

The one exception is the **"Wrong conversion"** button. If someone uses it and sends a
correction, three texts are saved: what they typed, what they got, and their correction. The
time, the source (web or telegram) and the direction are saved with them, but no name or id.

## Run it locally

```bash
uv sync
make web                                   # http://127.0.0.1:8000
TELEGRAM_BOT_TOKEN=... uv run python -m khmer_converter.bot
```

By default the engine uses the small lexicon bundled with it (3,000 words). For the full
lexicon (62,000 words), build it in a khmer-engine checkout with `make data` and point
`KHMER_ENGINE_DATA` at it:

```bash
KHMER_ENGINE_DATA=../khmer-engine/data/build make web
```

### Settings

| Variable | Used by | Default |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | bot | required |
| `KHMER_ENGINE_DATA` | both | the engine's bundled sample |
| `FEEDBACK_DB` | both | `feedback.db` (`/data/feedback.db` in Docker) |

## Set up the Telegram bot

1. Open [@BotFather](https://t.me/BotFather) in Telegram and send `/newbot`. Pick a name and
   a username. BotFather replies with the token.
2. Send `/setinline` to BotFather, choose the bot, and set a placeholder such as
   `sok sabay te`. This turns on inline mode.
3. Optionally, send `/setdescription` and `/setabouttext` to say what the bot does and that
   it does not keep messages.
4. Keep the token secret: put it in an environment variable or a `.env` file, never in the
   repository. If it leaks, send `/revoke` to BotFather.

The bot uses long polling, so it needs no public address, only a process that keeps running.

## Deploy

The Docker image holds the web page, the bot and the full lexicon, built from the engine's
open sources at the commit `uv.lock` pins. One process needs about 150 MB of memory and starts
in about 6 seconds, so the page and the bot together fit on a server with 512 MB; 1 GB is
comfortable.

### On any small server (VPS)

```bash
# once: install Docker (https://docs.docker.com/engine/install/), then
git clone https://github.com/pheasaKhmer/khmer-converter && cd khmer-converter
echo "TELEGRAM_BOT_TOKEN=123456:your-token" > .env
docker compose up -d --build
```

The page is then on port 8000 and the bot is running. Reports from both go to the
`feedback` volume. To serve the page over HTTPS on your own domain, put a reverse proxy in
front, for example [Caddy](https://caddyserver.com/), which gets certificates by itself:

```
converter.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

To update, `git pull` and run `docker compose up -d --build` again.

### On a hosting platform

Any platform that runs a Dockerfile works. Point it at this repository and give it:

- a persistent volume at `/data`, or reports are lost on every deploy;
- `TELEGRAM_BOT_TOKEN`, if it runs the bot;
- for the bot, an always-on service with the start command
  `python -m khmer_converter.bot`. Free tiers that put idle services to sleep suit the web
  page, but a sleeping bot stops answering.

### Without Docker

```bash
uv sync --no-dev
KHMER_ENGINE_DATA=/path/to/lexicon FEEDBACK_DB=/var/lib/khmer-converter/feedback.db \
  uv run uvicorn --factory khmer_converter.web:create_app --host 127.0.0.1 --port 8000 --no-access-log
```

## Using the reports

```bash
python -m khmer_converter.feedback feedback.db > corrections.tsv
```

This prints each report as a row for khmer-engine's `eval/testset.tsv` (romanized, Khmer,
reviewed), marked unreviewed. Check the rows, then add the good ones to the engine's test set
or its curated chat spellings.

## API

The page uses a small JSON API, which other tools can use too:

- `POST /api/convert` with `{"text": "...", "direction": "auto", "style": "chat"}`.
  `direction` is `auto`, `to_khmer` or `to_latin`; `style` is `chat` or `ungegn`. The
  response has the converted `text`, and for `to_khmer` the `words` with their `choices`.
- `POST /api/feedback` with `{"direction", "input", "output", "correction"}`
- `GET /healthz`

## Development

```bash
make check    # ruff and pytest (the tests use the engine's bundled sample)
make web      # run the page with reload
```

CI also builds the Docker image, starts it, and converts a phrase over HTTP.

## License

The code is [MIT](LICENSE). The Kantumruy Pro font is under the
[SIL Open Font License](src/khmer_converter/static/fonts/OFL.txt). The lexicon in the
Docker image keeps the licenses of its sources: CC BY 4.0 for the words and pronunciations,
and ODC-By 1.0 for the word counts (see khmer-engine's
[data/README.md](https://github.com/pheasaKhmer/khmer-engine/blob/main/data/README.md)).
