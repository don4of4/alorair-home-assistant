.PHONY: check test
check:
	uv run ruff check .
	uv run ruff format --check .
	uv run python scripts/check_distribution.py
	uv run pytest -q
test:
	uv run pytest -q
