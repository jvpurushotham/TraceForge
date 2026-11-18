#!/usr/bin/env bash
# Keeps all demo port-forwards alive (restarts one if its pod is replaced). Ctrl+C stops everything.
trap 'kill 0' INT TERM EXIT
fwd() { while true; do kubectl -n "$1" port-forward "svc/$2" "$3:$4" >/dev/null 2>&1; sleep 1; done; }
fwd traceforge-app order-service 8000 8000 &
fwd traceforge-app payment-service 8003 8003 &
fwd traceforge-app incident-service 8010 8010 &
fwd traceforge-observability kps-kube-prometheus-stack-prometheus 9090 9090 &
fwd traceforge-observability kps-kube-prometheus-stack-alertmanager 9093 9093 &
fwd traceforge-observability kps-grafana 3000 80 &
echo "Forwarding 8000 order, 8003 payment, 8010 incident, 9090 prometheus, 9093 alertmanager, 3000 grafana. Ctrl+C to stop."
wait
