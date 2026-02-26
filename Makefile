.PHONY: build test install clean

build:
	# TODO: swift build -c release
	# TODO: cd python && uv sync
	@echo "build: not yet implemented"

test:
	cd python && uv run pytest

install:
	# TODO: run scripts/install.sh
	@echo "install: not yet implemented"

clean:
	# TODO: rm -rf .build/ __pycache__/ *.pyc .venv/
	@echo "clean: not yet implemented"
