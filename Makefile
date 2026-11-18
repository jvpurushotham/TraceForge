# Optional shortcuts (Linux/macOS/WSL/Git Bash). Windows users can copy the docker compose commands instead.
P1 = -f docker-compose.yml
P2 = $(P1) -f docker-compose.phase2.yml
P3 = $(P2) -f docker-compose.phase3.yml

install:        ; python -m pip install -r requirements-dev.txt
test:           ; python -m pytest -q
evaluate:       ; python tests/evaluation/evaluate.py
local:          ; python scripts/run_local.py
phase1:         ; docker compose $(P1) up --build -d
phase2:         ; docker compose $(P2) up --build -d
phase3:         ; docker compose $(P3) up --build -d
down:           ; docker compose $(P3) down
reset:          ; docker compose $(P3) down -v
load:           ; python scripts/load_generator.py --rps 3 --duration 60
experiment:     ; python tests/fault-injection/run_experiment.py --scenario slow_payment
dashboards:     ; python scripts/gen_dashboards.py
.PHONY: install test evaluate local phase1 phase2 phase3 down reset load experiment dashboards
