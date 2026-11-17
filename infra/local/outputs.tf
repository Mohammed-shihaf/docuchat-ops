output "kubeconfig" {
  description = "Use with: export KUBECONFIG=<this path>"
  value       = local.kubeconfig
}

output "next_steps" {
  value = <<-EOT
    kubectl --kubeconfig ${local.kubeconfig} -n ${var.namespace} port-forward svc/docuchat-docuchat-api 8080:80      -> http://localhost:8080
    kubectl --kubeconfig ${local.kubeconfig} -n monitoring port-forward svc/monitoring-grafana 3000:80             -> http://localhost:3000 (admin / grafana_admin_password)
    kubectl --kubeconfig ${local.kubeconfig} -n argocd port-forward svc/argocd-server 8081:80                      -> http://localhost:8081
  EOT
}
