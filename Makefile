.PHONY: setup start stop run test install

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

# Install runtime + dev dependencies.
install:
	pip install -r requirements.txt -r requirements-dev.txt

# Run the test suite.
test:
	FLASK_DEBUG=1 DATABASE_URL="sqlite:///:memory:" pytest -q
