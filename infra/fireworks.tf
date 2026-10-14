# -----------------------------------------------------------------------------
# Fireworks on Foundry deployments (optional, var.deploy_fireworks) - pay-per-token only.
# Account + project: main.tf. Prerequisite check: scripts/check-fireworks-prereqs.sh (.ps1).
#
# Deployments on one resource are created one after another where possible. The default config contains one
# deployment (`fw`); the sequencing remains for users who explicitly add more models.
# -----------------------------------------------------------------------------

locals {
  fireworks_keys = sort(keys(var.fireworks_models))

  fireworks_specs = {
    for k, m in var.fireworks_models : k => merge(m, {
      deployment_name = coalesce(m.deployment_name, m.model)
    })
  }

  fireworks_first = local.fireworks_enabled ? {
    (local.fireworks_keys[0]) = local.fireworks_specs[local.fireworks_keys[0]]
  } : {}
  fireworks_rest = local.fireworks_enabled ? {
    for k in slice(local.fireworks_keys, 1, length(local.fireworks_keys)) : k => local.fireworks_specs[k]
  } : {}

  # VALIDATE: Fireworks deployments are documented as OpenAI v1 chat-completions compatible; the portal's Target URI
  # is expected on the services.ai.azure.com host (fallback: ai_endpoint_style = "openai").
  fireworks_base_url = local.fireworks_enabled ? "https://${azurerm_cognitive_account.fireworks[0].custom_subdomain_name}.${local.ai_host_suffix}/openai/v1/" : null

  fireworks_deployment_names = { for k, m in local.fireworks_specs : k => m.deployment_name }

  fireworks_data_zone_regions = ["eastus", "eastus2", "centralus", "northcentralus", "westus", "westus3"]
}

# Deploying Fireworks models needs "Foundry Owner" (formerly "Azure AI Owner") on the project according to
# Microsoft Learn; subscription Owner/Contributor is usually enough for ARM deployments. Opt-in if you get Forbidden.
# RBAC propagation can take a few minutes: if the first apply still fails, run terraform apply again.
resource "azurerm_role_assignment" "fireworks_deployer" {
  count = local.fireworks_enabled && var.fireworks_grant_deployer_role ? 1 : 0

  scope                = azurerm_cognitive_account_project.fireworks[0].id
  role_definition_name = var.fireworks_deployer_role
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_cognitive_deployment" "fireworks_first" {
  for_each = local.fireworks_first

  name                 = each.value.deployment_name
  cognitive_account_id = azurerm_cognitive_account.fireworks[0].id

  model {
    format  = var.fireworks_model_format # VALIDATE (see variables.tf)
    name    = each.value.model
    version = each.value.version # null = current default version (catalog shows "Version: 1")
  }

  sku {
    name     = each.value.sku
    capacity = each.value.capacity
  }

  # Fireworks deployments can take up to 30 minutes (provider default timeout is 30m).
  timeouts {
    create = "60m"
    delete = "60m"
  }

  depends_on = [
    azurerm_cognitive_account_project.fireworks,
    azurerm_role_assignment.fireworks_deployer,
  ]
}

resource "azurerm_cognitive_deployment" "fireworks_rest" {
  for_each = local.fireworks_rest

  name                 = each.value.deployment_name
  cognitive_account_id = azurerm_cognitive_account.fireworks[0].id

  model {
    format  = var.fireworks_model_format # VALIDATE (see variables.tf)
    name    = each.value.model
    version = each.value.version
  }

  sku {
    name     = each.value.sku
    capacity = each.value.capacity
  }

  timeouts {
    create = "60m"
    delete = "60m"
  }

  depends_on = [azurerm_cognitive_deployment.fireworks_first]
}

# Warning only (does not block): Data Zone Standard pay-per-token is offered in the US data zone only.
check "fireworks_region" {
  assert {
    condition = !local.fireworks_enabled || contains(local.fireworks_data_zone_regions, lower(var.fireworks_location)) || alltrue([
      for m in values(var.fireworks_models) : m.sku != "DataZoneStandard"
    ])
    error_message = "fireworks_location '${var.fireworks_location}' is not a Data Zone Standard (pay-per-token) region for Fireworks. Use one of: eastus, eastus2, centralus, northcentralus, westus, westus3."
  }
}
