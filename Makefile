.PHONY: dev backend frontend seed test lint

dev: ## run backend + frontend together
	@trap 'kill 0' INT; \
	(cd backend && uv run uvicorn app.main:app --reload --port 8000) & \
	(cd frontend && pnpm dev) & \
	wait

backend:
	cd backend && uv run uvicorn app.main:app --reload --port 8000

frontend:
	cd frontend && pnpm dev

seed:
	cd backend && uv run python ../scripts/seed.py

test:
	cd backend && uv run pytest -q

lint:
	cd backend && uv run ruff check app tests
