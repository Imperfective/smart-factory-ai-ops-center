# SF-AIC2 — all targets run locally on the Mac. No cloud, no real equipment.
PY=.venv/bin/python
.PHONY: install env-check test demo api web jev-smoke experiment-smoke experiment-jev clean

install:            ## uv venv + python deps + web deps
	uv venv --python 3.12 .venv && UV_HTTP_TIMEOUT=900 uv pip install --python $(PY) -e ".[dev,analysis]" && cd apps/web && npm install --no-audit --no-fund

env-check:          ## hardware, env flags, key presence (never prints the key)
	@uname -m; sw_vers | head -2; echo "memsize_bytes=$$(sysctl -n hw.memsize)"; $(PY) --version
	@$(PY) -c "import os; from dotenv import load_dotenv; load_dotenv('.env'); print('SIMULATION_MODE', os.environ.get('SIMULATION_MODE')); print('CONTROL_TARGET', os.environ.get('CONTROL_TARGET')); print('JEV_ENABLED', os.environ.get('JEV_ENABLED')); k=os.environ.get('TYPESAFE_API_KEY',''); print('TYPESAFE_API_KEY present:', bool(k) and not k.startswith('REPLACE_'))"
	@$(PY) -c "import simpy, pandapower, fastapi; print('simpy', simpy.__version__, 'pandapower', pandapower.__version__, 'fastapi', fastapi.__version__)"
	@git check-ignore -q .env && echo ".env is git-ignored"

test:               ## python unit/integration/safety tests + starter + web tests
	$(PY) -m pytest -q
	$(PY) -m unittest discover -s tests -p "test_starter.py" -q
	cd apps/web && npx vitest run

demo:               ## offline end-to-end: S2 scenario, arm E with MOCK_JEV, saved to results/runs/
	$(PY) -m sfaic2.cli demo --preset S2 --arm E

api:                ## FastAPI backend on :8000 (reads .env; fail-closed outside SIMULATION_MODE)
	$(PY) -m uvicorn apps.api.app.main:app --host 127.0.0.1 --port 8000 --reload

web:                ## React dev server on http://localhost:5173 (proxies /api and /ws to :8000)
	cd apps/web && npx vite --port 5173 --strictPort

jev-smoke:          ## ONE real TypeSafe Jev call (uses .env key; ~0.00002 USD); records results/jev_smoke/
	JEV_ENABLED=true $(PY) -m sfaic2.cli jev-smoke

experiment-smoke:   ## mock-provider matrix (9 families × 3 seeds × 5 arms) -> results/experiments/smoke/
	$(PY) -m sfaic2.experiments.runner --exp smoke --seeds 3 --provider mock

experiment-jev:     ## real TypeSafe Jev matrix, arms A–E (per-run cap raised; global cap in runner)
	JEV_ENABLED=true JEV_MAX_CALLS_PER_RUN=1500 JEV_DAILY_BUDGET_USD=2.0 $(PY) -m sfaic2.experiments.runner --exp jev_pilot --seeds 2 --provider typesafe --arms A B C D E --families normal F02 F03 F04 F05 --save-runs

clean:
	rm -rf results/runs results/experiments results/sfaic2.sqlite

factory-vision: ## MVTec AD (user-downloaded, CC BY-NC-SA 4.0) → YOLO11n fine-tune on MPS → held-out perception log for the dashboard
	.venv/bin/python -m sfaic2.cli factory-vision --step all --epochs 30

research-db: ## Build results/research.sqlite (public data + measured stats + every run + raw rows + SQL aggregates + paired CIs)
	.venv/bin/python -m sfaic2.cli research-db

v3-test: ## SF AI OS v3 acceptance tests (Phases 1-5)
	.venv/bin/python -m pytest -q tests/unit/test_v3.py

v3-mock: ## v3 R0-R5 paired matrix with MockJev (software check) + replay oracle
	.venv/bin/python -m sfaic2.v3.experiment --exp main --provider mock --seeds 0-29 --arms R0,R1,R2,R3,R4,R5 --oracle-seeds 0-9 --raw-seeds 0
	.venv/bin/python -m sfaic2.v3.report results/v3/mock/main results/v3/mock/oracle

v3-pilot: ## v3 real TypeSafe Jev pilot (needs JEV_ENABLED=true + key in .env)
	.venv/bin/python -m sfaic2.v3.experiment --exp pilot --provider typesafe --scenarios S2,S3,S4,S5,S8,S9,S10,S11,S12,S13,S14,S15 --loads medium --seeds 0-2 --arms R1,R2,R3,R4 --threads 6 --raw-seeds 0
	.venv/bin/python -m sfaic2.v3.report results/v3/real_jev/pilot

v3-batch: ## paired random-course batch with MockJev (software check) -> results/v3/live_runs
	.venv/bin/python -m sfaic2.v3.live_results --n 100 --provider mock

v3-batch-real: ## paired random-course batch with the real TypeSafe Jev (needs key in .env; capped by --budget)
	JEV_ENABLED=true .venv/bin/python -m sfaic2.v3.live_results --n 30 --provider typesafe --budget 1.0

v3-report: ## regenerate CSVs + docs/v3/RESULTS_random_courses.md from the stored runs
	.venv/bin/python -m sfaic2.v3.live_results --report

