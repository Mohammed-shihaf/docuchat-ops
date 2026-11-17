# OPTIONAL paid path. The default, free way to run DocuChat is deploy/free-vm (Oracle Always Free).
# Production-shaped Azure platform: AKS + Key Vault, with ACR / Log Analytics / an extra node pool OFF by default.
# Cost guardrails: free AKS control plane, one small node, a monthly budget alert, and `az aks stop` when idle.
# CI only runs `terraform validate`; `apply` is manual - always read `terraform plan` first.

terraform {
  required_version = ">= 1.6"
  required_providers {
    azurerm = { source = "hashicorp/azurerm", version = "~> 4.0" }
  }
  # Remote state for teams (uncomment and fill in):
  # backend "azurerm" {
  #   resource_group_name  = "rg-tfstate"
  #   storage_account_name = "sttfstatedocuchat"
  #   container_name       = "tfstate"
  #   key                  = "docuchat.tfstate"
  # }
}

provider "azurerm" {
  features {}
  subscription_id = var.subscription_id
}

data "azurerm_client_config" "current" {}

locals {
  name = "${var.prefix}-${var.environment}"
  tags = merge(var.tags, { app = "docuchat", environment = var.environment, managed_by = "terraform" })
}

resource "azurerm_resource_group" "this" {
  name     = "rg-${local.name}"
  location = var.location
  tags     = local.tags
}

resource "azurerm_log_analytics_workspace" "this" {
  count               = var.enable_log_analytics ? 1 : 0 # billed per GB ingested
  name                = "log-${local.name}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = local.tags
}

resource "azurerm_container_registry" "this" {
  count               = var.enable_acr ? 1 : 0 # ~USD 5/month; GHCR is free for public images
  name                = replace("acr${local.name}", "-", "")
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  sku                 = "Basic"
  admin_enabled       = false
  tags                = local.tags
}

resource "azurerm_kubernetes_cluster" "this" {
  name                      = "aks-${local.name}"
  location                  = azurerm_resource_group.this.location
  resource_group_name       = azurerm_resource_group.this.name
  dns_prefix                = local.name
  kubernetes_version        = var.kubernetes_version
  sku_tier                  = var.environment == "prod" ? "Standard" : "Free"
  oidc_issuer_enabled       = true
  workload_identity_enabled = true
  azure_policy_enabled      = true
  tags                      = local.tags

  default_node_pool {
    name                         = "system"
    vm_size                      = var.system_vm_size
    node_count                   = 1
    only_critical_addons_enabled = var.enable_app_pool # single shared node when the app pool is off
    upgrade_settings { max_surge = "33%" }
  }

  identity { type = "SystemAssigned" }

  network_profile {
    network_plugin    = "azure"
    network_policy    = "azure" # enforces the chart's NetworkPolicy (only the API can reach Qdrant)
    load_balancer_sku = "standard"
  }

  dynamic "oms_agent" {
    for_each = var.enable_log_analytics ? [1] : []
    content {
      log_analytics_workspace_id = azurerm_log_analytics_workspace.this[0].id
    }
  }

  key_vault_secrets_provider {
    secret_rotation_enabled = true
  }
}

resource "azurerm_kubernetes_cluster_node_pool" "apps" {
  count                 = var.enable_app_pool ? 1 : 0
  name                  = "apps"
  kubernetes_cluster_id = azurerm_kubernetes_cluster.this.id
  vm_size               = var.app_vm_size
  auto_scaling_enabled  = true
  min_count             = var.app_min_nodes
  max_count             = var.app_max_nodes
  tags                  = local.tags
  upgrade_settings { max_surge = "33%" }
}

# Let the cluster pull images from ACR without registry passwords.
resource "azurerm_role_assignment" "acr_pull" {
  count                            = var.enable_acr ? 1 : 0
  scope                            = azurerm_container_registry.this[0].id
  role_definition_name             = "AcrPull"
  principal_id                     = azurerm_kubernetes_cluster.this.kubelet_identity[0].object_id
  skip_service_principal_aad_check = true
}

resource "azurerm_key_vault" "this" {
  name                       = substr(replace("kv-${local.name}", "_", "-"), 0, 24)
  location                   = azurerm_resource_group.this.location
  resource_group_name        = azurerm_resource_group.this.name
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = true
  purge_protection_enabled   = var.environment == "prod"
  soft_delete_retention_days = 7
  tags                       = local.tags
}

# The LLM API key lives in Key Vault; the app reads it through the CSI secrets driver + workload identity.
resource "azurerm_user_assigned_identity" "app" {
  name                = "id-${local.name}-api"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags
}

resource "azurerm_federated_identity_credential" "app" {
  name                = "docuchat-api"
  resource_group_name = azurerm_resource_group.this.name
  parent_id           = azurerm_user_assigned_identity.app.id
  audience            = ["api://AzureADTokenExchange"]
  issuer              = azurerm_kubernetes_cluster.this.oidc_issuer_url
  subject             = "system:serviceaccount:${var.app_namespace}:docuchat-api"
}

resource "azurerm_role_assignment" "app_kv_reader" {
  scope                = azurerm_key_vault.this.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

# Free guardrail: email alerts at 50%, 80% and 100% of the monthly budget.
resource "azurerm_consumption_budget_resource_group" "this" {
  count             = length(var.budget_alert_emails) > 0 ? 1 : 0
  name              = "budget-${local.name}"
  resource_group_id = azurerm_resource_group.this.id
  amount            = var.monthly_budget
  time_grain        = "Monthly"

  time_period {
    start_date = formatdate("YYYY-MM-01'T'00:00:00Z", plantimestamp())
  }

  dynamic "notification" {
    for_each = [50, 80, 100]
    content {
      enabled        = true
      threshold      = notification.value
      operator       = "GreaterThanOrEqualTo"
      contact_emails = var.budget_alert_emails
    }
  }

  lifecycle {
    ignore_changes = [time_period]
  }
}
