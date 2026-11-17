variable "subscription_id" {
  description = "Azure subscription to deploy into"
  type        = string
  default     = "00000000-0000-0000-0000-000000000000" # placeholder so `validate` works without credentials
}

variable "prefix" {
  type    = string
  default = "docuchat"
}

variable "environment" {
  type    = string
  default = "dev"
  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be dev, staging or prod"
  }
}

variable "location" {
  type    = string
  default = "centralindia"
}

variable "kubernetes_version" {
  type    = string
  default = null # latest supported by AKS
}

variable "system_vm_size" {
  description = "B2ms (2 vCPU / 8 GB, burstable) fits the whole app on one node"
  type        = string
  default     = "Standard_B2ms"
}

variable "app_vm_size" {
  description = "Needs ~4 GB RAM per node: Qdrant keeps vectors in memory"
  type        = string
  default     = "Standard_B2ms"
}

variable "enable_app_pool" {
  description = "Separate autoscaling node pool for apps. Off = one shared node (cheapest)."
  type        = bool
  default     = false
}

variable "enable_acr" {
  description = "Azure Container Registry (paid). Off = pull the public image from GHCR (free)."
  type        = bool
  default     = false
}

variable "enable_log_analytics" {
  description = "Container Insights (billed per GB). Off = use the in-cluster Prometheus/Grafana."
  type        = bool
  default     = false
}

variable "monthly_budget" {
  description = "Monthly budget for the resource group, in the subscription's billing currency"
  type        = number
  default     = 20
}

variable "budget_alert_emails" {
  description = "Who gets budget alerts. Empty = no budget resource."
  type        = list(string)
  default     = []
}

variable "app_min_nodes" {
  type    = number
  default = 1
}

variable "app_max_nodes" {
  type    = number
  default = 3
}

variable "app_namespace" {
  type    = string
  default = "docuchat"
}

variable "tags" {
  type    = map(string)
  default = {}
}
