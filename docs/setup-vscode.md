# Run TraceForge on your machine with VS Code

## 0. Install once
| Tool | Needed for | Check |
|---|---|---|
| Python 3.12 or newer | tests, local mode | `python --version` |
| VS Code + extensions *Python*, *Docker*, *YAML* | editing/debugging (VS Code will suggest them) | |
| Docker Desktop (WSL2 backend on Windows), >= 6 GB RAM for Docker | Phases 2-3 (full stack) | `docker compose version` |
| (optional) Minikube, kubectl, Helm | Kubernetes part | |

## 1. Open the project
Unzip `TraceForge.zip` -> VS Code -> **File > Open Folder...** -> the `TraceForge` folder. Open a terminal (**Terminal > New Terminal**).

## 2. Python environment
```bash
python -m venv .venv
# Windows PowerShell:   .venv\Scripts\Activate.ps1        (if blocked: Set-ExecutionPolicy -Scope Process RemoteSigned)
# Windows cmd:          .venv\Scripts\activate.bat
# macOS / Linux / WSL:  source .venv/bin/activate
pip install -r requirements-dev.txt
```
VS Code: **Ctrl+Shift+P > Python: Select Interpreter >** `.venv`. (`.vscode/settings.json` already enables pytest.)

## 3. Prove it works (no Docker)
```bash
python -m pytest -q                       # expect: 24 passed
python tests/evaluation/evaluate.py       # rule engine vs 11 labeled cases
```

## 4. Phase 1 without Docker (quickest)
```bash
python scripts/run_local.py --with-incident
```
Starts product/inventory/payment/order (+ incident service) on ports 8001/8002/8003/8000/8010 with SQLite. In a **second** terminal:
```bash
python scripts/load_generator.py --rps 3 --duration 20
```
Open http://localhost:8003/docs -> `POST /admin/fault` -> `{"delay_ms": 2000}` -> Execute, run the load again and watch p95 jump.
Clear with `DELETE /admin/fault`. Stop everything with Ctrl+C.
(In this mode there is no Prometheus/Tempo/Loki, so incidents will honestly report *missing telemetry*. For the real thing use Docker.)
You can also debug a service: **Run and Debug** panel -> *Debug order-service*.

## 5. Full platform with Docker (all three phases)
Start Docker Desktop, then in the project folder:
```bash
# Phase 1 only
docker compose up --build -d
# Phase 1 + 2 (observability)
docker compose -f docker-compose.yml -f docker-compose.phase2.yml up --build -d
# Phase 1 + 2 + 3 (everything)
docker compose -f docker-compose.yml -f docker-compose.phase2.yml -f docker-compose.phase3.yml up --build -d
docker compose ps          # wait until everything is "running"/"healthy" (first start: 1-3 min)
```
Note: switching phases? Add `--remove-orphans` or run `docker compose ... down` with the files you used.

Then generate traffic: `python scripts/load_generator.py --rps 3 --duration 240` and follow the demo in the README
(Grafana http://localhost:3000, incident UI http://localhost:8010/).

Real measurements of detection/diagnosis (needs all three phases):
```bash
python tests/fault-injection/run_experiment.py --scenario slow_payment    # or --all (about 30 minutes)
```
Stop: `docker compose -f docker-compose.yml -f docker-compose.phase2.yml -f docker-compose.phase3.yml down` (add `-v` to delete data).

## 6. Optional AI explanations
Copy `.env.example` to `.env`, fill `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL`, then
`docker compose ... up -d incident-service`. Never commit `.env`. Without a key everything still works.

## 7. Optional Kubernetes (Minikube) / EKS
See `infrastructure/kubernetes/README.md`. On Windows use WSL2 or Git Bash for `install-minikube.sh`.

## Ports
8000 order - 8001 product - 8002 inventory - 8003 payment - 8010 incident - 3000 Grafana - 9090 Prometheus - 9093 Alertmanager - 3100 Loki - 3200 Tempo
