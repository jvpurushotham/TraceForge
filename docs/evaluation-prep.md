# Evaluation prep - mapped to the INT334 rubric

## Marks and where the evidence is
| Component | Marks | Your evidence |
|---|---|---|
| Report | 20 | Template below + `evidence/` screenshots |
| Presentation | 10 | Demo order below, every member speaks |
| Viva | 20 | Q&A below; each member explains their own part |
| Implementation | 50 | Helm charts, Minikube run, HPA, PVC, RBAC, Prometheus/Grafana/Alertmanager, tests, GitHub |

## Report outline (about 15-20 pages)
1. **Problem statement and objectives (4):** distributed failures are hard to diagnose; monitoring says *something* is wrong, not *why*. Objectives: deploy a multi-service app on Kubernetes, monitor it, detect failures, rank likely causes with evidence.
2. **Background and methodology (4):** containers, Kubernetes objects used, Helm, Prometheus/PromQL, Grafana, Alertmanager, OpenTelemetry, Tempo, Loki; why rules first and an optional LLM second.
3. **Architecture (3):** diagram with the two namespaces, deployments, services, PVC, Ingress, monitoring flow, incident pipeline (use the README diagram + `docs/architecture.md`).
4. **Implementation evidence (4):** excerpts of Deployment, Service, ConfigMap, Secret, RBAC, HPA, Ingress, PVC, CronJob YAML; Helm commands; screenshots from Part H, each with a one-line caption.
5. **Testing, results, conclusion (3):** 24 automated tests; failure tests (slow dependency, crash loop, OOMKilled, bad rollout + rollback, missing telemetry, backup/restore); `docs/live-results.md` numbers; **limitations** (single-node Minikube, simulated services, small rule set, fixtures vs live, no EKS deployment unless you really did it); future work.
6. **Formatting and references (2):** consistent terminology; cite Kubernetes, Helm, Prometheus, Grafana, OpenTelemetry docs.

## Presentation (10-12 min)
1 min problem - 2 min architecture - 5 min live demo (F5 scaling, F8 fault -> alert -> diagnosis, F9 OOMKilled) - 1 min tests/results - 1 min limits/future.
Split by person: e.g. A = app + Dockerfiles, B = Kubernetes/Helm, C = Prometheus/Grafana/Alertmanager, D = incident engine/AI. Each explains their own files.

## Be careful about claims (rubric rule 5)
Say "demonstrated" only for what you ran. EKS/Terraform and AWS CloudWatch are *designed, not deployed* unless you actually deploy them. The offline accuracy table is from fixtures, not a live system. The AI layer is optional and off unless you configure a key.

## Viva questions you should be able to answer
**Containers/Kubernetes (5):** container vs VM; what a Dockerfile layer is; Pod vs ReplicaSet vs Deployment; what a Namespace is; control plane vs worker components; Minikube vs kubeadm vs EKS.
**Deployment/config (5):** what each field in `helm/demo-app/templates/services.yaml` does (labels, selector, probes, requests/limits, `envFrom`); ConfigMap vs Secret (base64 is *not* encryption); what the `incident-service` Role/RoleBinding allows and why it is read-only; `kubectl apply` vs `helm upgrade`; `helm rollback`.
**Networking/storage/scaling (5):** ClusterIP vs NodePort vs LoadBalancer vs Ingress; how Service selectors find Pods; PV vs PVC vs StorageClass and why Postgres survives a pod deletion; requests vs limits and what OOMKilled means; HPA formula `desired = ceil(current x currentCPU / targetCPU)` and why metrics-server is needed; HPA vs VPA.
**Monitoring/automation (5):** how Prometheus discovers targets (ServiceMonitor); explain `rate()` and `histogram_quantile`; what each alert rule in `observability/prometheus/alert-rules.yml` does and why `for:` exists; Alertmanager grouping and the webhook; why metrics, traces and logs are different signals; what Helm automates; one security measure you implemented.
**Project-specific:** why the score is *not* a probability; how the engine decides "insufficient evidence"; what stops the LLM from inventing a cause.

If you did not write a part, read it before the viva: a file you cannot explain costs marks even if it works.
