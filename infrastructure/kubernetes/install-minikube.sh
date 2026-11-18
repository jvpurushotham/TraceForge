#!/usr/bin/env bash
# Install the whole TraceForge platform on a LOCAL Minikube cluster.
# Requirements: docker, minikube, kubectl, helm.  (Windows: run from WSL2 or Git Bash.)
# Best-effort automation - read each step; chart versions change. Not executed in the authoring environment.
set -euo pipefail
cd "$(dirname "$0")/../.."
OBS=traceforge-observability
APP=traceforge-app

minikube status >/dev/null 2>&1 || minikube start --cpus=4 --memory=6144 --driver=docker
minikube addons enable metrics-server   # needed by the HPA
minikube addons enable ingress          # nginx ingress controller
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add grafana https://grafana.github.io/helm-charts
helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts
helm repo update

echo "== 1/5 build images and load them into minikube"
for s in product-service inventory-service payment-service order-service incident-service; do
  port=8000; case $s in product-service) port=8001;; inventory-service) port=8002;; payment-service) port=8003;; incident-service) port=8010;; esac
  docker build -f services/Dockerfile --build-arg SERVICE=$s --build-arg PORT=$port -t traceforge/$s:latest .
  minikube image load traceforge/$s:latest
done

echo "== 2/5 observability stack ($OBS)"
kubectl create namespace $OBS --dry-run=client -o yaml | kubectl apply -f -
helm upgrade --install kps prometheus-community/kube-prometheus-stack -n $OBS -f infrastructure/kubernetes/observability/kube-prometheus-stack-values.yaml
helm upgrade --install tempo grafana/tempo -n $OBS -f infrastructure/kubernetes/observability/tempo-values.yaml
helm upgrade --install loki grafana/loki -n $OBS -f infrastructure/kubernetes/observability/loki-values.yaml
helm upgrade --install otel-collector open-telemetry/opentelemetry-collector -n $OBS -f infrastructure/kubernetes/observability/otel-collector-values.yaml

echo "== 3/5 Grafana dashboards"
for f in observability/grafana/dashboards/*.json; do
  n=$(basename "$f" .json)
  kubectl -n $OBS create configmap "dash-$n" --from-file="$f" --dry-run=client -o yaml | kubectl apply -f -
  kubectl -n $OBS label configmap "dash-$n" grafana_dashboard=1 --overwrite
done

echo "== 4/5 demo application ($APP)"
kubectl create namespace $APP --dry-run=client -o yaml | kubectl apply -f -
helm upgrade --install demo-app helm/demo-app -n $APP
helm upgrade --install traceforge helm/traceforge -n $APP

echo "== 5/5 done. Port-forward:"
echo "  kubectl -n $APP port-forward svc/order-service 8000:8000"
echo "  kubectl -n $APP port-forward svc/incident-service 8010:8010"
echo "  kubectl -n $OBS port-forward svc/kps-grafana 3000:80      # admin / admin"
