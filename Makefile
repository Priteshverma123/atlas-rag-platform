.PHONY: up down logs bootstrap test lint fmt typecheck eval diagrams load-gateway load-query

up:            ## start the core stack (mock LLM unless API keys are set)
	docker compose up -d --build --wait

down:
	docker compose down

logs:
	docker compose logs -f --tail=100 api gateway worker-ingest worker-light

bootstrap:     ## create a tenant + admin key (prints the key once)
	curl -fsS -X POST localhost:8000/v1/admin/bootstrap -H "x-bootstrap-token: $${ATLAS_BOOTSTRAP_TOKEN:-change-me-bootstrap}" -H "content-type: application/json" -d '{"tenant_name":"demo"}'

test:
	uv run pytest

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests

fmt:
	uv run ruff format src tests
	uv run ruff check --fix src tests

typecheck:
	uv run mypy

eval:          ## make eval KEY=atl_...
	uv run atlas-eval run --dataset evals/datasets/smoke.jsonl --corpus evals/corpus --key $(KEY)

diagrams:
	cd docs/diagrams && python build_diagrams.py && python build_flows.py

load-gateway:  ## gateway overhead benchmark against the mock provider
	k6 run loadtest/gateway.js

load-query:    ## make load-query KEY=atl_... COLLECTION=<id>
	ATLAS_KEY=$(KEY) COLLECTION=$(COLLECTION) k6 run loadtest/query.js
