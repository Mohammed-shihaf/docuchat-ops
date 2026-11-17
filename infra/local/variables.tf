variable "cluster_name" {
  type    = string
  default = "docuchat"
}

variable "kubernetes_version" {
  description = "kindest/node image tag"
  type        = string
  default     = "v1.33.1"
}

variable "namespace" {
  type    = string
  default = "docuchat"
}

variable "image" {
  description = "Locally built app image (repository:tag), loaded into kind"
  type        = string
  default     = "docuchat-ops-api:latest"
  validation {
    condition     = length(split(":", var.image)) == 2
    error_message = "image must be repository:tag"
  }
}

variable "llm_api_key" {
  description = "LLM API key (e.g. Groq). Pass via TF_VAR_llm_api_key - never commit it."
  type        = string
  sensitive   = true
}

variable "grafana_admin_password" {
  type      = string
  sensitive = true
  default   = "docuchat-admin"
}

# Laptop-friendly defaults: the app alone needs ~2 GB. Monitoring (+~1.5 GB) and Argo CD (+~1 GB)
# are opt-in - enable them on a machine with 16 GB+ free for Docker, or on a real cluster.
variable "enable_monitoring" {
  type    = bool
  default = false
}

variable "enable_argocd" {
  type    = bool
  default = false
}

variable "kube_prometheus_stack_version" {
  type    = string
  default = "93.3.1"
}

variable "argocd_version" {
  type    = string
  default = "10.18.0"
}
