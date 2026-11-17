# Azure Kubernetes Service (AKS) basics

Azure Kubernetes Service (AKS) is a managed Kubernetes service. Azure runs the Kubernetes control plane for you at no charge; you pay only for the worker nodes (virtual machines) in your node pools.

A node pool is a group of nodes with the same VM size. Every cluster has a system node pool for critical system pods; user node pools run application workloads. Node pools can scale automatically with the cluster autoscaler.

AKS integrates with Microsoft Entra ID for authentication and Azure RBAC for authorization. Workload identity lets pods access Azure resources such as Key Vault without storing secrets in the cluster.

Container images are usually pulled from Azure Container Registry (ACR). Attaching an ACR to an AKS cluster grants the kubelet identity pull access.

Upgrades: AKS supports upgrading the control plane and node pools to newer Kubernetes versions. Node surge settings control how many extra nodes are added during an upgrade to reduce disruption.
