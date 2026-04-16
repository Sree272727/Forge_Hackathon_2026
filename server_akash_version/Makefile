# Intelligent Excel Services Makefile
# Repository: https://github.com/ignitedata-ai/intellegent-excel-services

.PHONY: help install dev dev-setup test lint format clean build up down logs shell init-db

# Default target
help:
	@echo "Available commands:"
	@echo "  dev-setup   - Full development setup (install, db, migrate, seed)"
	@echo "  install     - Install dependencies"
	@echo "  dev         - Run development server"
	@echo "  test        - Run tests"
	@echo "  test-cov    - Run tests with coverage"
	@echo "  lint        - Run linting"
	@echo "  format      - Format code"
	@echo "  clean       - Clean up generated files"
	@echo "  build       - Build Docker images"
	@echo "  up          - Start services with Docker Compose"
	@echo "  down        - Stop services"
	@echo "  logs        - View service logs"
	@echo "  shell       - Open shell in backend container"
	@echo "  migrate     - Run database migrations"
	@echo "  init-db     - Initialize database with seed data"
	@echo "  check       - Run all quality checks"

install:
	uv sync --frozen --no-cache

dev-setup:
	@echo "Setting up development environment..."
	@echo "Step 1: Installing dependencies..."
	uv sync --frozen --no-cache
	@echo "Step 2: Starting database services..."
	docker compose up -d postgres redis
	@echo "Waiting for database to be ready..."
	@sleep 5
	@echo "Step 3: Running database migrations..."
	uv run alembic upgrade head
	@echo "Step 4: Initializing database with seed data..."
	uv run python scripts/init_data.py
	@echo "Development setup complete!"

dev:
	uv run python main.py

test:
	uv run pytest -v

test-cov:
	uv run pytest --cov=core --cov=api --cov-report=html --cov-report=term

lint:
	uv run ruff check .
	uv run mypy .

format:
	uv run ruff format .
	uv run ruff check --fix .

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache
	rm -rf htmlcov
	rm -rf .coverage
	rm -rf .mypy_cache
	rm -rf .ruff_cache

build:
	docker-compose build

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f excel-services

shell:
	docker compose exec excel-services /bin/bash

migrate:
	uv run alembic upgrade head

init-db:
	@echo "Initializing database with seed data..."
	uv run python scripts/init_data.py

check: lint test
	@echo "All quality checks passed!"


# Database operations
db-up:
	docker compose up -d postgres redis

db-down:
	docker compose down postgres redis

# UI operations
ui-install:
	cd ui && npm install

ui-dev:
	cd ui && npm run dev

ui-build:
	cd ui && npm run build
