.PHONY: help install install-dev test seed run check pages pages-check security clean docker-up docker-down

help:
	@echo "Available commands:"
	@echo "  make install      Install runtime dependencies"
	@echo "  make install-dev  Install runtime + security tooling (bandit, pip-audit)"
	@echo "  make check        Preflight: seed, full pytest suite, GitHub Pages freshness"
	@echo "  make test         Execute pytest suite"
	@echo "  make seed         Re-seed 500 journeys & 100 prompt audits"
	@echo "  make run          Launch FastAPI and Streamlit dashboard"
	@echo "  make pages        Rebuild index.html and docs/index.html (GitHub Pages)"
	@echo "  make pages-check  Fail if the committed Pages output is stale"
	@echo "  make security     Static analysis (bandit) + dependency audit (pip-audit)"
	@echo "  make docker-up    Start Docker containers (requires JWT_SECRET, TENANT_CLIENT_SECRETS)"
	@echo "  make docker-down  Stop Docker containers"

install:
	pip install -r requirements.txt

install-dev:
	pip install -r requirements-dev.txt

test:
	pytest tests/ -v

seed:
	python -m src.seed_journeys

check:
	python run_local.py --check

run:
	python run_local.py

pages:
	python scripts/build_pages.py

pages-check:
	python scripts/build_pages.py --check

security:
	bandit -q -r src scripts -ll
	pip-audit -r requirements.txt

docker-up:
	docker compose up --build -d

docker-down:
	docker compose down
