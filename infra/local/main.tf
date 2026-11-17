# Local platform: kind cluster + monitoring + Argo CD + DocuChat, all from one `terraform apply`.
# The kubeconfig is written to this folder only - your ~/.kube/config (and any work clusters) are never touched.

terraform {
  required_version = ">= 1.6"
  required_providers {
    kind       = { source = "tehcyx/kind", version = "~> 0.9" }
    helm       = { source = "hashicorp/helm", version = "~> 2.17" }
    kubernetes = { source = "hashicorp/kubernetes", version = "~> 2.36" }
    null       = { source = "hashicorp/null", version = "~> 3.2" }
  }
}

locals {
  kubeconfig = abspath("${path.module}/.kubeconfig")
  chart_path = abspath("${path.module}/../../charts/docuchat")
}

resource "kind_cluster" "this" {
  name            = var.cluster_name
  node_image      = "kindest/node:${var.kubernetes_version}"
  kubeconfig_path = local.kubeconfig
  wait_for_ready  = true

  kind_config {
    kind        = "Cluster"
    api_version = "kind.x-k8s.io/v1alpha4"
    node {
      role = "control-plane"
    }
  }
}

# Connection details come straight from the cluster resource (not the kubeconfig file),
# so providers work on the very first apply, before the file exists.
provider "helm" {
  kubernetes {
    host                   = kind_cluster.this.endpoint
    cluster_ca_certificate = kind_cluster.this.cluster_ca_certificate
    client_certificate     = kind_cluster.this.client_certificate
    client_key             = kind_cluster.this.client_key
  }
}

provider "kubernetes" {
  host                   = kind_cluster.this.endpoint
  cluster_ca_certificate = kind_cluster.this.cluster_ca_certificate
  client_certificate     = kind_cluster.this.client_certificate
  client_key             = kind_cluster.this.client_key
}

# --- Observability: Prometheus Operator, Prometheus, Grafana, Alertmanager ---------
resource "helm_release" "monitoring" {
  count            = var.enable_monitoring ? 1 : 0
  name             = "monitoring"
  repository       = "https://prometheus-community.github.io/helm-charts"
  chart            = "kube-prometheus-stack"
  version          = var.kube_prometheus_stack_version
  namespace        = "monitoring"
  create_namespace = true
  timeout          = 900
  values = [yamlencode({
    grafana = {
      adminPassword = var.grafana_admin_password
      sidecar       = { dashboards = { enabled = true, searchNamespace = "ALL" } }
    }
    prometheus = { prometheusSpec = {
      # Pick up ServiceMonitors/PrometheusRules from every namespace, not just this release.
      serviceMonitorSelectorNilUsesHelmValues = false
      ruleSelectorNilUsesHelmValues           = false
      retention                               = "2d"
      resources                               = { requests = { cpu = "100m", memory = "256Mi" }, limits = { memory = "768Mi" } }
    } }
    # Laptop-sized: drop components that add RAM but little value on a single-node local cluster.
    alertmanager               = { enabled = false }
    "prometheus-node-exporter" = { enabled = false }
    nodeExporter               = { enabled = false }
    kubeEtcd                   = { enabled = false }
    kubeScheduler              = { enabled = false }
    kubeControllerManager      = { enabled = false }
    kubeProxy                  = { enabled = false }
  })]
  depends_on = [null_resource.load_image] # one heavy step at a time - avoids memory spikes on laptops
}

# --- GitOps: Argo CD ----------------------------------------------------------------
resource "helm_release" "argocd" {
  count            = var.enable_argocd ? 1 : 0
  name             = "argocd"
  repository       = "https://argoproj.github.io/argo-helm"
  chart            = "argo-cd"
  version          = var.argocd_version
  namespace        = "argocd"
  create_namespace = true
  timeout          = 900
  values = [yamlencode({
    dex            = { enabled = false }
    notifications  = { enabled = false }
    applicationSet = { replicas = 0 }
    configs        = { params = { "server.insecure" = true } }
  })]
  depends_on = [helm_release.monitoring]
}

# --- DocuChat ------------------------------------------------------------------------
resource "kubernetes_namespace" "app" {
  metadata { name = var.namespace }
}

resource "kubernetes_secret" "llm" {
  metadata {
    name      = "docuchat-llm"
    namespace = kubernetes_namespace.app.metadata[0].name
  }
  data = { LLM_API_KEY = var.llm_api_key }
}

# Load the locally built image into the kind node (no registry needed for local dev).
resource "null_resource" "load_image" {
  triggers = { image = var.image, cluster = kind_cluster.this.id }
  provisioner "local-exec" {
    command = "kind load docker-image ${var.image} --name ${var.cluster_name}"
  }
}

resource "helm_release" "docuchat" {
  name      = "docuchat"
  chart     = local.chart_path
  namespace = kubernetes_namespace.app.metadata[0].name
  timeout   = 600
  values = [yamlencode({
    image               = { repository = split(":", var.image)[0], tag = split(":", var.image)[1], pullPolicy = "Never" }
    replicaCount        = 1
    autoscaling         = { enabled = false }
    podDisruptionBudget = { minAvailable = 0 }
    app                 = { trustProxyHeaders = false }
    resources           = { requests = { cpu = "100m", memory = "512Mi" }, limits = { memory = "1536Mi" } }
    metrics             = { serviceMonitor = { enabled = var.enable_monitoring }, grafanaDashboard = { enabled = var.enable_monitoring } }
  })]
  depends_on = [helm_release.monitoring, null_resource.load_image, kubernetes_secret.llm]
}
