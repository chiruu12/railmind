.PHONY: dev backend frontend seed test lint demo

PORT ?= 8000

dev: ## run backend + frontend together (override backend port: make dev PORT=8010)
	@trap 'kill 0' INT; \
	(cd backend && uv run uvicorn app.main:app --reload --port $(PORT)) & \
	(cd frontend && BACKEND_URL=http://localhost:$(PORT) pnpm dev) & \
	wait

backend:
	cd backend && uv run uvicorn app.main:app --reload --port $(PORT)

frontend:
	cd frontend && BACKEND_URL=http://localhost:$(PORT) pnpm dev

demo: ## headless demo cascade rehearsal (no LLM keys needed: AGENT_LLM=off)
	cd backend && PYTHONPATH=. uv run python ../scripts/demo.py

seed:
	cd backend && uv run python ../scripts/seed.py

test:
	cd backend && uv run pytest -q

lint:
	cd backend && uv run ruff check app tests
