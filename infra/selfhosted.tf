# -----------------------------------------------------------------------------
# Optional self-hosted small model: Ollama on Azure Container Apps (Consumption, CPU only).
# Ollama exposes an OpenAI-compatible API at https://<fqdn>/v1/ (no GPU -> expect a few tokens/second).
#
# SECURITY: Ollama has no authentication. The endpoint is public for the duration of the workshop;
# destroy it afterwards (terraform destroy, or set deploy_selfhosted_model = false and apply).
# -----------------------------------------------------------------------------

resource "azurerm_container_app_environment" "env" {
  count = var.deploy_selfhosted_model ? 1 : 0

  name                       = "cae-tw-${local.team}-${local.suffix}"
  location                   = azurerm_resource_group.rg.location
  resource_group_name        = azurerm_resource_group.rg.name
  logs_destination           = "log-analytics" # azurerm 5.x: required to use log_analytics_workspace_id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.law.id
  tags                       = local.tags

  # Workload profiles (v2) environment: its Consumption profile allows up to 4 vCPU / 8 GiB per replica.
  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }
}

resource "azurerm_container_app" "ollama" {
  count = var.deploy_selfhosted_model ? 1 : 0

  name                         = "ollama-${local.suffix}"
  container_app_environment_id = azurerm_container_app_environment.env[0].id
  resource_group_name          = azurerm_resource_group.rg.name
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  tags                         = local.tags

  template {
    min_replicas = 1 # keep the model warm (the model is pulled at start-up)
    max_replicas = 1

    container {
      name   = "ollama"
      image  = "docker.io/ollama/ollama:latest"
      cpu    = var.selfhosted_cpu
      memory = var.selfhosted_memory

      # Start the server, pull the model once, then keep the server in the foreground.
      command = ["/bin/sh", "-c"]
      args    = ["ollama serve & sleep 5 && ollama pull ${var.selfhosted_model} && wait"]

      env {
        name  = "OLLAMA_HOST"
        value = "0.0.0.0:11434"
      }
      env {
        name  = "OLLAMA_KEEP_ALIVE"
        value = "24h"
      }
      env {
        name  = "OLLAMA_NUM_PARALLEL"
        value = "2"
      }
    }
  }

  ingress {
    external_enabled = true
    target_port      = 11434
    transport        = "auto"

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }
}

locals {
  selfhosted_base_url = var.deploy_selfhosted_model ? "https://${azurerm_container_app.ollama[0].ingress[0].fqdn}/v1/" : null
}
