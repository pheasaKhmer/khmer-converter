.PHONY: check lint format test web

check: lint test

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .
	uv run ruff check --fix .

test:
	uv run pytest

# Run the web page on http://127.0.0.1:8000
web:
	uv run uvicorn --factory khmer_converter.web:create_app --reload
