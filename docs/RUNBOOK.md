# TraceForge runbook - from scratch to a rubric-ready Kubernetes demo (INT334)

**Where everything runs:** on your own Mac. Open the project folder in VS Code and use **Terminal > New Terminal** for every
command below. Docker Desktop provides the containers; Minikube runs a one-node Kubernetes cluster *inside* Docker Desktop.
Use separate terminal tabs where a step says "keep running".

The rubric gives 50 of 100 marks to the Kubernetes implementation, so **Part D-F (Minikube) is the demo**. Docker Compose
(Part C) is only a quick rehearsal and your fallback.

---
## Part A - One-time setup (30 min)
1. Docker Desktop: Settings > Resources > **Memory 8 GB, CPUs 4** (kube-prometheus-stack is heavy). Start it.
2. Install tools (Homebrew): `brew install minikube kubectl helm git`
3. Check: `docker version && minikube version && kubectl version --client && helm version`
4. In the project folder:
   ```bash
   python3 -m venv venv && source venv/bin/activate
   pip install -r requirements-dev.txt
   python -m pytest -q          # expect 24 passed   (NOT "python -m test")
   python tests/evaluation/evaluate.py
   ```

## Part B - Understand the app without Kubernetes (15 min)
```bash
python scripts/run_local.py --with-incident        # keep running (Ctrl+C stops)
# second tab:
python scripts/load_generator.py --rps 3 --duration 20
```
Open http://localhost:8003/docs -> `POST /admin/fault` `{"delay_ms": 2000}` -> run load again -> latency jumps. Clear with `DELETE /admin/fault`. Ctrl+C the first tab.

## Part C - (optional) Docker Compose rehearsal (20 min)
```bash
docker compose -f docker-compose.yml -f docker-compose.phase2.yml -f docker-compose.phase3.yml up --build -d
python scripts/load_generator.py --rps 3 --duration 120
# Grafana http://localhost:3000   incident UI http://localhost:8010/
docker compose -f docker-compose.yml -f docker-compose.phase2.yml -f docker-compose.phase3.yml down -v   # IMPORTANT: free RAM before Minikube
```
Screenshot these as evidence if you like (trace in Tempo, logs in Loki).

## Part D - Deploy on Minikube (the main work, 1-2 hours first time)
```bash
chmod +x infrastructure/kubernetes/install-minikube.sh
./infrastructure/kubernetes/install-minikube.sh
```
It starts Minikube, enables `metrics-server` + `ingress`, builds the 5 images and loads them into the cluster, installs
kube-prometheus-stack, Tempo, Loki, the OTel Collector (namespace `traceforge-observability`), Grafana dashboards, then the
`demo-app` and `traceforge` Helm releases (namespace `traceforge-app`).

Wait and verify (repeat until all Running/Completed, can take 5-10 min):
```bash
kubectl get pods -A
helm list -A
kubectl -n traceforge-app get deploy,svc,pvc,hpa,ingress,cronjob,configmap,secret
```
**This script was never executed by the person who wrote it.** If a step fails, read the error, fix, re-run (it is idempotent).
Keep a notes file of every problem + fix - it becomes your troubleshooting section and viva material.

| Problem | Fix |
|---|---|
| `Pending` pods / OOM | Docker Desktop memory < 8 GB, or `minikube delete` then `minikube start --cpus=4 --memory=6144` |
| `ImagePullBackOff` on traceforge/* | `minikube image ls \| grep traceforge`; reload: `minikube image load traceforge/order-service:latest` |
| `no matches for kind ServiceMonitor` | kube-prometheus-stack not ready yet: `helm -n traceforge-observability list`, wait, then `helm upgrade --install demo-app helm/demo-app -n traceforge-app` |
| Helm rejects a chart value | `helm show values <chart>`, adjust `infrastructure/kubernetes/observability/*.yaml` |
| incident-service `CrashLoopBackOff` | `kubectl -n traceforge-app logs deploy/incident-service` (Postgres not ready yet is normal for a minute) |
| Tempo has no data | `kubectl -n traceforge-observability get svc tempo` - if its HTTP port is not 3100, set `tempoUrl` in `helm/traceforge/values.yaml` and the Grafana datasource, `helm upgrade` |

## Part E - Open the services (keep each running in its own tab)
```bash
kubectl -n traceforge-app port-forward svc/order-service 8000:8000
kubectl -n traceforge-app port-forward svc/payment-service 8003:8003
kubectl -n traceforge-app port-forward svc/incident-service 8010:8010
kubectl -n traceforge-observability port-forward svc/kps-grafana 3000:80                                  # admin / admin
kubectl -n traceforge-observability port-forward svc/kps-kube-prometheus-stack-prometheus 9090:9090
kubectl -n traceforge-observability port-forward svc/kps-kube-prometheus-stack-alertmanager 9093:9093
```
Smoke test: `curl -X POST localhost:8000/orders -H 'content-type: application/json' -d '{"product_id":"p-1001"}'` (expect 201) and open http://localhost:8010/.

Ingress (optional demo): `minikube tunnel` (keep running, asks for password), add `127.0.0.1 traceforge.local incident.traceforge.local`
to `/etc/hosts`, then `curl http://traceforge.local/products/p-1001` and open http://incident.traceforge.local/.

## Part F - The demonstrations that earn marks (rehearse each; screenshot each)
Rule 5 of the rubric: only demonstrated features count. Each block = one rubric topic.

**F1 Workloads, Namespaces, Services (CO1/CO2)**
`kubectl get ns` - `kubectl -n traceforge-app get deploy,rs,pods -o wide` - `kubectl -n traceforge-app describe deploy order-service` (point at replicas, selectors, probes, resources).

**F2 ConfigMap, Secret, RBAC (CO2/CO6)**
`kubectl -n traceforge-app get configmap app-config -o yaml` - `kubectl -n traceforge-app get secret postgres-credentials` (values are base64, not in Git) -
`kubectl auth can-i list pods -n traceforge-app --as=system:serviceaccount:traceforge-app:incident-service` -> **yes**; same with `delete pods` -> **no**.

**F3 Networking (CO3)** - ClusterIP services (default, internal), port-forward (debug access), Ingress above. Be ready to explain NodePort vs LoadBalancer.

**F4 Storage (CO3)** `kubectl -n traceforge-app get pvc,pv,storageclass` -> create an order, then
`kubectl -n traceforge-app delete pod postgres-0`, wait until Running, `curl localhost:8000/orders` -> the order survived (PVC).

**F5 Scaling (CO3)** Watch: `kubectl -n traceforge-app get hpa -w` (tab 1) and run `python scripts/load_generator.py --rps 40 --duration 180` (tab 2).
Replicas should rise toward 4, then fall after the load stops. If it does not scale, lower the target:
`helm upgrade demo-app helm/demo-app -n traceforge-app --set hpa.cpuTargetPercent=20`. Explain requests/limits and the HPA formula.

**F6 Prometheus + PromQL (CO4/CO5)** http://localhost:9090/targets (service discovery via ServiceMonitor) and run:
`sum by (service) (rate(http_requests_total[1m]))` - `histogram_quantile(0.95, sum by (le,service)(rate(http_request_duration_seconds_bucket[1m])))` -
`sum by (service)(rate(http_requests_total{status=~"5.."}[1m]))` - `kube_pod_container_status_restarts_total{namespace="traceforge-app"}`

**F7 Grafana (CO4/CO5)** http://localhost:3000 -> dashboards A-D (run load first). Explore -> Tempo (one trace across 4 services) and Loki.

**F8 Alert -> TraceForge diagnosis (the innovation)**
```bash
python scripts/load_generator.py --rps 3 --duration 300         # tab 1
curl -X POST localhost:8003/admin/fault -H 'content-type: application/json' -d '{"delay_ms": 3000}'
```
Within ~1-2 min: alert in http://localhost:9093, incident in http://localhost:8010/ with top cause `payment-service / slow_downstream_dependency`.
Clear: `curl -X DELETE localhost:8003/admin/fault`.

**F9 Failure conditions on Kubernetes (testing marks)**
* Crash loop: `curl -X POST localhost:8003/admin/crash` twice, ~15 s apart (re-run the payment port-forward after each crash) - `kubectl -n traceforge-app get pods -w` shows RESTARTS rising.
* **OOMKilled:** `curl -X POST localhost:8003/admin/leak -H 'content-type: application/json' -d '{"mb":400}'` (limit is 256Mi) -> `kubectl -n traceforge-app describe pod -l app=payment-service` shows `Last State: Terminated, Reason: OOMKilled`. TraceForge reads this from the Kubernetes API (`memory_exhaustion`).
* Bad rollout + rollback: `helm upgrade demo-app helm/demo-app -n traceforge-app --set image.tag=broken` -> pods `ImagePullBackOff` ->
  `helm history demo-app -n traceforge-app` -> `helm rollback demo-app 1 -n traceforge-app` -> pods healthy again.
* Missing telemetry: `kubectl -n traceforge-observability scale deploy/otel-collector-opentelemetry-collector --replicas=0`, create an incident, re-run diagnosis - it lists the gap instead of guessing. Scale back to 1.

**F10 Backup / restore / retention (CO6)**
```bash
kubectl -n traceforge-app create job --from=cronjob/postgres-backup manual-backup-1
kubectl -n traceforge-app logs job/manual-backup-1          # lists the .dump files
# restore (edit the file name from the list above):
sed 's/__FILE__/traceforge-YYYYMMDD-HHMMSS.dump/; s/__DB__/traceforge/' infrastructure/kubernetes/restore-job.yaml | kubectl -n traceforge-app apply -f -
kubectl -n traceforge-app logs job/postgres-restore        # ends with RESTORE-OK
```
The CronJob also deletes dumps older than `backup.retentionDays`.

**F11 Helm automation (CO6)** `helm list -A` - `helm get values demo-app -n traceforge-app` - `helm template demo-app helm/demo-app | less` (show templating) - a fresh install is one script.

## Part G - GitHub repository (5 marks)
```bash
git init && git add . && git status           # confirm .env, venv/, *.db are NOT listed
git commit -m "Phase 1: microservices and fault injection"
```
Make several meaningful commits (e.g. one per phase / feature, each teammate committing their own work), create an empty repo on github.com,
then `git remote add origin <url> && git branch -M main && git push -u origin main`. Never commit API keys.

## Part H - Evidence to collect (put in an `evidence/` folder, use in the report)
Screenshots: `kubectl get pods -A` - `helm list -A` - `get hpa` scaling up/down - PVC/StorageClass - RBAC `can-i` yes/no - Prometheus targets + a PromQL graph -
all 4 Grafana dashboards - a trace in Tempo - Loki logs - Alertmanager alert - incident page with diagnosis - OOMKilled `describe` - helm rollback - backup/restore logs -
`pytest` output - `docs/live-results.md` (note: the automated `run_experiment.py` needs the *Docker Compose* stack, so run it in Part C, not on Minikube).

## Part I - Suggested order of work
1. Session 1: Parts A, B, C. 2. Session 2: Part D until everything is Running. 3. Session 3: Parts E-F, collecting screenshots as you go.
4. Session 4: report + GitHub (see `docs/evaluation-prep.md`). 5. Session 5: full rehearsal with a timer, record a backup video.
