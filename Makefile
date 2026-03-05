.PHONY: help dev stop restart logs logs-api logs-worker logs-beat clean build test shell shell-api shell-worker db-shell deploy

help:
	@echo "════════════════════════════════════════════════════════════"
	@echo "  Binayah Properties - Available Commands"
	@echo "════════════════════════════════════════════════════════════"
	@echo ""
	@echo "Development:"
	@echo "  make dev          - Start development environment"
	@echo "  make stop         - Stop all services"
	@echo "  make restart      - Restart all services"
	@echo "  make build        - Rebuild containers"
	@echo ""
	@echo "Logs:"
	@echo "  make logs         - Show all logs"
	@echo "  make logs-api     - Show API logs only"
	@echo "  make logs-worker  - Show Celery worker logs"
	@echo "  make logs-beat    - Show Celery beat logs"
	@echo ""
	@echo "Database:"
	@echo "  make db-shell     - Open MongoDB shell"
	@echo ""
	@echo "Testing:"
	@echo "  make test         - Run all tests"
	@echo "  make lint         - Run linters"
	@echo ""
	@echo "Shell Access:"
	@echo "  make shell        - Open shell in API container"
	@echo "  make shell-api    - Open Python shell in API container"
	@echo ""
	@echo "Cleanup:"
	@echo "  make clean        - Clean build artifacts"
	@echo "  make clean-all    - Remove containers and volumes"
	@echo ""
	@echo "Utilities:"
	@echo "  make scrape       - Run scraper manually"
	@echo "  make score        - Score all draft posts"
	@echo "  make health       - Check service health"
	@echo ""

dev:
	@echo "🚀 Starting development environment..."
	@./start.sh start

stop:
	@echo "🛑 Stopping all services..."
	@docker compose down

restart:
	@echo "🔄 Restarting all services..."
	@docker compose restart

build:
	@echo "🔨 Building containers..."
	@docker compose build

logs:
	@docker compose logs -f

logs-api:
	@docker compose logs -f api

logs-worker:
	@docker compose logs -f worker

logs-beat:
	@docker compose logs -f beat

db-shell:
	@mongosh "$(shell grep MONGODB_URI .env | cut -d '=' -f2-)"

test:
	@docker compose exec api pytest tests/ -v

lint:
	@docker compose exec api flake8 app/
	@docker compose exec api black --check app/

format:
	@docker compose exec api black app/

shell:
	@docker compose exec api /bin/bash

shell-api:
	@docker compose exec api python

clean:
	@echo "🧹 Cleaning build artifacts..."
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@find . -type f -name "*.pyo" -delete 2>/dev/null || true
	@rm -rf apps/web/dist apps/web/build 2>/dev/null || true
	@echo "✅ Cleanup complete!"

clean-all:
	@echo "⚠️  WARNING: This will delete all data!"
	@read -p "Continue? (yes/no): " confirm; \
	if [ "$$confirm" = "yes" ]; then \
		docker compose down -v --rmi all; \
		docker system prune -f; \
		echo "✅ Everything removed!"; \
	fi

ps:
	@docker compose ps

health:
	@curl -s http://localhost:8000/health | python -m json.tool || echo "❌ API not responding"

fetch:
	@docker compose exec api python -c "import asyncio; from app.services.newsgen.pipeline import run_pipeline; asyncio.run(run_pipeline())"

score:
	@docker compose exec api python -c "import asyncio; from app.services.newsgen.validation import score_all_drafts; asyncio.run(score_all_drafts())"
