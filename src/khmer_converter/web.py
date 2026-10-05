"""The web page and its small JSON API.

    uvicorn --factory khmer_converter.web:create_app

Environment:
    KHMER_ENGINE_DATA  lexicon directory for the engine (default: its bundled sample)
    FEEDBACK_DB        SQLite file for wrong-conversion reports (default: feedback.db)

Conversions are not stored or logged. Only reports sent with the "wrong conversion"
form reach the feedback database. The page loads nothing from other sites.
"""

import os
from importlib.resources import files
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from khmer_converter.feedback import MAX_LENGTH, FeedbackStore
from khmer_converter.service import MAX_INPUT, Converter, direction_of

STATIC = Path(str(files("khmer_converter") / "static"))

# The page uses inline style and script and nothing from other origins.
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


class ConvertRequest(BaseModel):
    text: str = Field(max_length=MAX_INPUT)
    direction: Literal["auto", "to_khmer", "to_latin"] = "auto"
    style: Literal["chat", "ungegn"] = "chat"


class WordOut(BaseModel):
    typed: str
    start: int
    end: int
    choices: list[str]


class ConvertResponse(BaseModel):
    direction: Literal["to_khmer", "to_latin"]
    text: str
    words: list[WordOut]


class FeedbackRequest(BaseModel):
    direction: Literal["to_khmer", "to_latin"]
    input: str = Field(min_length=1, max_length=MAX_LENGTH)
    output: str = Field(max_length=MAX_LENGTH)
    correction: str = Field(min_length=1, max_length=MAX_LENGTH)


def create_app(converter: Converter | None = None, store: FeedbackStore | None = None) -> FastAPI:
    app = FastAPI(title="khmer-converter", docs_url=None, redoc_url=None, openapi_url=None)
    converter = converter or Converter()
    store = store or FeedbackStore(os.environ.get("FEEDBACK_DB", "feedback.db"))
    page = (STATIC / "index.html").read_text(encoding="utf-8")

    @app.middleware("http")
    async def security_headers(request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return page

    @app.get("/healthz")
    def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/api/convert")
    def convert(request: ConvertRequest) -> ConvertResponse:
        direction = request.direction
        if direction == "auto":
            direction = direction_of(request.text)
        if direction == "to_latin":
            result = converter.to_latin(request.text, request.style)
        else:
            result = converter.to_khmer(request.text)
        words = [
            WordOut(typed=w.typed, start=w.start, end=w.end, choices=w.choices)
            for w in result.words
        ]
        return ConvertResponse(direction=result.direction, text=result.text, words=words)

    @app.post("/api/feedback")
    def feedback(request: FeedbackRequest) -> dict[str, bool]:
        try:
            store.add(
                source="web",
                direction=request.direction,
                input=request.input,
                output=request.output,
                correction=request.correction,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        return {"ok": True}

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
