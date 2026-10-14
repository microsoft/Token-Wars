resource "random_string" "suffix" {
  length  = 5
  lower   = true
  upper   = false
  numeric = true
  special = false
}

data "azurerm_client_config" "current" {}

locals {
  team                = var.team_name
  suffix              = random_string.suffix.result
  resource_group_name = coalesce(var.resource_group_name, "rg-tokenwars-${var.team_name}")
  apim_enabled        = var.deploy_apim
  secondary_enabled   = var.deploy_secondary_region
  fireworks_enabled   = var.deploy_fireworks

  tags = merge({
    workload        = "token-wars-microhack"
    team            = var.team_name
    SecurityControl = "Ignore"
  }, var.tags)

  # Foundry resource names double as custom subdomains (globally unique, max 64 chars)
  ai_account_name           = "ais-tw-${local.team}-${local.suffix}"
  ai_secondary_account_name = "ais-tw-${local.team}-${local.suffix}-2"
  fireworks_account_name    = "ais-tw-${local.team}-${local.suffix}-fw"

  # OpenAI-compatible v1 endpoint used by the apps for every Foundry-hosted model.
  # Microsoft Learn (v1 API): base_url accepts both https://<name>.services.ai.azure.com/openai/v1/ (new Foundry domain,
  # default) and https://<name>.openai.azure.com/openai/v1/ (fallback: ai_endpoint_style = "openai").
  ai_host_suffix = var.ai_endpoint_style == "foundry" ? "services.ai.azure.com" : "openai.azure.com"
  ai_base_url    = "https://${azurerm_cognitive_account.ai.custom_subdomain_name}.${local.ai_host_suffix}/openai/v1/"

  foundry_project_display_name = "Token Wars – ${var.team_name}"
}

resource "azurerm_resource_group" "rg" {
  name     = local.resource_group_name
  location = var.location
  tags     = local.tags
}

# -----------------------------------------------------------------------------
# Primary Microsoft Foundry resource (new Foundry model: kind AIServices + project management)
# Model deployments live on the resource; the project is the workspace teams open in ai.azure.com.
# No hub / Azure ML workspace resources are needed any more.
# -----------------------------------------------------------------------------
resource "azurerm_cognitive_account" "ai" {
  name                          = local.ai_account_name
  location                      = azurerm_resource_group.rg.location
  resource_group_name           = azurerm_resource_group.rg.name
  kind                          = "AIServices"
  sku_name                      = "S0"
  custom_subdomain_name         = local.ai_account_name # required for projects and Entra ID auth
  project_management_enabled    = true                  # = "Foundry resource" (allowProjectManagement)
  local_auth_enabled            = true                  # the apps use API keys; APIM uses its managed identity
  public_network_access_enabled = true

  identity {
    type = "SystemAssigned" # required for projects
  }

  tags = local.tags
}

resource "azurerm_cognitive_account_project" "ai" {
  name                 = var.foundry_project_name
  cognitive_account_id = azurerm_cognitive_account.ai.id
  location             = azurerm_resource_group.rg.location
  display_name         = local.foundry_project_display_name
  description          = "ByteCart Support Copilot - Token Wars microhack (team ${var.team_name})."

  identity {
    type = "SystemAssigned"
  }

  tags = local.tags
}

# -----------------------------------------------------------------------------
# Optional secondary Foundry resource (failover target for the APIM backend pool). No project needed.
# -----------------------------------------------------------------------------
resource "azurerm_cognitive_account" "ai_secondary" {
  count = local.secondary_enabled ? 1 : 0

  name                          = local.ai_secondary_account_name
  location                      = var.secondary_location
  resource_group_name           = azurerm_resource_group.rg.name
  kind                          = "AIServices"
  sku_name                      = "S0"
  custom_subdomain_name         = local.ai_secondary_account_name
  project_management_enabled    = true
  local_auth_enabled            = true
  public_network_access_enabled = true

  identity {
    type = "SystemAssigned"
  }

  tags = local.tags
}

# -----------------------------------------------------------------------------
# Optional Fireworks on Foundry resource (US region, pay-per-token) + project. Deployments: fireworks.tf
# -----------------------------------------------------------------------------
resource "azurerm_cognitive_account" "fireworks" {
  count = local.fireworks_enabled ? 1 : 0

  name                          = local.fireworks_account_name
  location                      = var.fireworks_location
  resource_group_name           = azurerm_resource_group.rg.name
  kind                          = "AIServices"
  sku_name                      = "S0"
  custom_subdomain_name         = local.fireworks_account_name
  project_management_enabled    = true
  local_auth_enabled            = true # the apps use FIREWORKS_AI_API_KEY (key of this resource)
  public_network_access_enabled = true

  identity {
    type = "SystemAssigned"
  }

  tags = local.tags
}

resource "azurerm_cognitive_account_project" "fireworks" {
  count = local.fireworks_enabled ? 1 : 0

  name                 = var.foundry_project_name
  cognitive_account_id = azurerm_cognitive_account.fireworks[0].id
  location             = var.fireworks_location
  display_name         = "${local.foundry_project_display_name} (Fireworks)"
  description          = "Fireworks on Foundry models for Challenge 2.4 (pay-per-token)."

  identity {
    type = "SystemAssigned"
  }

  tags = local.tags
}
