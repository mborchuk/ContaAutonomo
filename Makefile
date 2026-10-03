.PHONY: setup start stop run test install lock

# Generate a .env with a strong SECRET_KEY (won't overwrite an existing one).
setup:
	@if [ -f .env ]; then \
		echo ".env already exists — leaving it untouched."; \
	else \
		echo "SECRET_KEY=$$(python3 -c 'import secrets; print(secrets.token_hex(32))')" > .env; \
		echo "FLASK_DEBUG=0" >> .env; \
		echo ".env created. Review it, then run: make start"; \
	fi

# Production-like run via Docker.
start:
	docker compose up -d

stop:
	docker compose down

# Local dev server.
run:
	FLASK_DEBUG=1 python3 app.py

# Install runtime + dev dependencies (hashed lock files).
install:
	pip install -r requirements.txt -r requirements-dev.txt

# Recompile the lock files from requirements*.in on the image's Python
# (same base image as the Dockerfile, so markers match production).
PY_IMAGE := $(shell sed -n 's/^FROM //p' Dockerfile | head -1)
lock:
	docker run --rm -v "$(CURDIR)":/src -w /src -e CUSTOM_COMPILE_COMMAND="make lock" $(PY_IMAGE) \
		sh -c "pip install -q pip-tools && \
		pip-compile -q --generate-hashes --strip-extras --no-emit-index-url -o requirements.txt requirements.in && \
		pip-compile -q --generate-hashes --strip-extras --no-emit-index-url -o requirements-dev.txt requirements-dev.in"

# Run the test suite.
test:
	FLASK_DEBUG=1 DATABASE_URL="sqlite:///:memory:" pytest -q
