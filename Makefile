.PHONY: build test install clean

build:
	swift build -c release
	cd python && uv sync

test:
	swift test
	cd python && uv run pytest

install:
	scripts/install.sh

clean:
	rm -rf .build/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name '*.pyc' -delete 2>/dev/null || true
	rm -rf python/.venv/
