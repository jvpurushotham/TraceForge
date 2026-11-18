# Kubernetes / EKS (Phase 3 deployment)

* `install-minikube.sh` - builds the images, loads them into Minikube and installs the observability stack, the demo
  app and the incident service with Helm.  **Best-effort**: it was not executed in the environment that generated this
  project. Chart value keys (kube-prometheus-stack, tempo, loki, opentelemetry-collector) change between releases - if
  Helm rejects a key, run `helm show values <chart>` and adjust the file in `observability/`.
* `helm/demo-app` and `helm/traceforge` are the project's own charts. Install both into the **same** namespace
  (`traceforge-app`): the incident service reuses the `postgres-credentials` secret created by `demo-app`.
* AWS EKS: `../terraform/main.tf` is an optional short-lived cluster; push images to ECR, set `image.registry`,
  `helm install` the same charts, then `terraform destroy` to stop the bill.
