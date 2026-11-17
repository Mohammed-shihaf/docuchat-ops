output "resource_group" {
  value = azurerm_resource_group.this.name
}

output "aks_name" {
  value = azurerm_kubernetes_cluster.this.name
}

output "acr_login_server" {
  value = var.enable_acr ? azurerm_container_registry.this[0].login_server : "not created (using GHCR)"
}

output "stop_when_idle" {
  description = "A stopped AKS cluster costs nothing for compute"
  value       = "az aks stop -g ${azurerm_resource_group.this.name} -n ${azurerm_kubernetes_cluster.this.name}"
}

output "key_vault_name" {
  value = azurerm_key_vault.this.name
}

output "app_identity_client_id" {
  description = "Annotate the docuchat-api ServiceAccount with azure.workload.identity/client-id"
  value       = azurerm_user_assigned_identity.app.client_id
}

output "get_credentials" {
  value = "az aks get-credentials -g ${azurerm_resource_group.this.name} -n ${azurerm_kubernetes_cluster.this.name} --file ./aks.kubeconfig"
}
