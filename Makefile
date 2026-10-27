.PHONY: help install test seed run check clean docker-up docker-down

help:
	@echo "Available commands:"
	@echo "  make install     Install required dependencies"
	@echo "  make check       Run preflight validation & test suite"
	@echo "  make test        Execute pytest suite"
	@echo "  make seed        Seed 500 journeys & 100 prompt audits"
	@echo "  make run         Launch FastAPI and Streamlit dashboard"
	@echo "  make docker-up   Start Docker containers"
	@echo "  make docker-down Stop Docker containers"

install:
	pip install -r requirements.txt

test:
	pytest tests/ -v

seed:
	python -m src.seed_journeys

check:
	python run_local.py --check

run:
	python run_local.py

docker-up:
	docker compose up --build -d

docker-down:
	docker compose down
