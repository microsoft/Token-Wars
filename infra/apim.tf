# -----------------------------------------------------------------------------
# Azure API Management as required AI gateway (only the judge bypasses it)
#
#   client --(api-key: <APIM subscription key>)--> APIM /openai/v1/...  --(managed identity)--> Foundry /openai/v1/...
#
# Backends are created with azapi because azurerm_api_management_backend cannot model load-balanced
# pools / circuit breakers consistently across provider versions.
#
# Backend host for the Azure OpenAI deployments: https://<subdomain>.openai.azure.com/openai
# Microsoft Learn's v1 API page documents both <name>.openai.azure.com/openai/v1 and <name>.services.ai.azure.com/openai/v1;
# its managed-identity (Entra ID) examples use the openai.azure.com host, and it is what the APIM "Azure OpenAI v1"
# import wizard targets, so the gateway keeps it (independent of ai_endpoint_style, which only affects models.json).
# -----------------------------------------------------------------------------

locals {
  primary_backend_name    = "aoai-primary"
  secondary_backend_name  = "aoai-secondary"
  pool_backend_name       = "aoai-pool"
  fireworks_backend_name  = "fireworks"
  selfhosted_backend_name = "selfhosted"
  apim_fireworks_enabled  = local.apim_enabled && local.fireworks_enabled
  # backend used by the failover part of the policy: the pool if a secondary region exists, otherwise the primary
  failover_backend_name = local.secondary_enabled ? local.pool_backend_name : local.primary_backend_name
  # subscription whose key is written to .env for the apps
  app_consumer = contains(var.apim_consumers, "support-squad") ? "support-squad" : var.apim_consumers[0]

  apim_role_accounts = merge(
    { primary = azurerm_cognitive_account.ai.id },
    local.secondary_enabled ? { secondary = azurerm_cognitive_account.ai_secondary[0].id } : {},
    local.fireworks_enabled ? { fireworks = azurerm_cognitive_account.fireworks[0].id } : {}
  )
  # Data-plane roles for keyless inference through the OpenAI v1 API (role IDs, stable across renames):
  #   Cognitive Services OpenAI User  5e0bd9bd-7b93-4f28-af87-19fc36ad61bd  (OpenAI models)
  #   Cognitive Services User         a97b65f3-24c7-4388-baec-2e87135dc908  (all models incl. partner/open models)
  # The 2026 Foundry role rename (Azure AI User/Owner/Account Owner/Project Manager -> Foundry User/Owner/...) does
  # not affect these two roles. "Foundry User" on the resource would also work but grants far more data actions.
  apim_roles = ["Cognitive Services OpenAI User", "Cognitive Services User"]
  apim_role_assignments = local.apim_enabled ? {
    for pair in setproduct(keys(local.apim_role_accounts), local.apim_roles) :
    "${pair[0]}|${pair[1]}" => { scope = local.apim_role_accounts[pair[0]], role = pair[1] }
  } : {}

  # Multi-provider failover: Azure deployment name (the "model" the app sends) -> Fireworks deployment name.
  # Default: balanced and premium both fail over to the single fw deployment.
  failover_model_map = var.failover_model_map != null ? var.failover_model_map : merge(
    contains(keys(var.fireworks_models), "fw") ? {
      (local.deployment_names.balanced) = local.fireworks_deployment_names["fw"]
      (local.deployment_names.premium)  = local.fireworks_deployment_names["fw"]
    } : {}
  )

  # api_key auth: the backend injects the key header on every forwarded request (key kept in a secret named value)
  fireworks_backend_credentials = var.fireworks_backend_auth == "api_key" ? {
    credentials = {
      header = {
        "api-key" = ["{{fireworks-api-key}}"]
      }
    }
  } : {}

  circuit_breaker = {
    rules = [{
      name = "throttle-breaker"
      failureCondition = {
        count            = 3
        interval         = "PT1M"
        statusCodeRanges = [{ min = 429, max = 429 }, { min = 500, max = 503 }]
      }
      tripDuration     = "PT1M"
      acceptRetryAfter = true
    }]
  }
}

resource "azurerm_api_management" "apim" {
  count = local.apim_enabled ? 1 : 0

  name                = "apim-tw-${local.team}-${local.suffix}"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  publisher_name      = "ByteCart Token Wars"
  publisher_email     = var.apim_publisher_email
  sku_name            = var.apim_sku

  identity {
    type = "SystemAssigned"
  }

  tags = local.tags
}

# APIM's managed identity may call the Foundry resource(s) (keyless backend auth)
resource "azurerm_role_assignment" "apim_ai" {
  for_each = local.apim_role_assignments

  scope                = each.value.scope
  role_definition_name = each.value.role
  principal_id         = azurerm_api_management.apim[0].identity[0].principal_id
  principal_type       = "ServicePrincipal"
}

# -----------------------------------------------------------------------------
# Backends
# -----------------------------------------------------------------------------
resource "azapi_resource" "backend_primary" {
  count = local.apim_enabled ? 1 : 0

  type                      = "Microsoft.ApiManagement/service/backends@2024-05-01"
  name                      = local.primary_backend_name
  parent_id                 = azurerm_api_management.apim[0].id
  schema_validation_enabled = false

  body = {
    properties = {
      description    = "Primary Foundry resource (${var.location})"
      url            = "https://${azurerm_cognitive_account.ai.custom_subdomain_name}.openai.azure.com/openai"
      protocol       = "http"
      circuitBreaker = local.circuit_breaker
    }
  }
}

resource "azapi_resource" "backend_secondary" {
  count = local.apim_enabled && local.secondary_enabled ? 1 : 0

  type                      = "Microsoft.ApiManagement/service/backends@2024-05-01"
  name                      = local.secondary_backend_name
  parent_id                 = azurerm_api_management.apim[0].id
  schema_validation_enabled = false

  body = {
    properties = {
      description    = "Secondary Foundry resource (${var.secondary_location})"
      url            = "https://${azurerm_cognitive_account.ai_secondary[0].custom_subdomain_name}.openai.azure.com/openai"
      protocol       = "http"
      circuitBreaker = local.circuit_breaker
    }
  }
}

# Priority-based pool: all traffic to the primary; the secondary only while the primary's circuit breaker is open.
resource "azapi_resource" "backend_pool" {
  count = local.apim_enabled && local.secondary_enabled ? 1 : 0

  type                      = "Microsoft.ApiManagement/service/backends@2024-05-01"
  name                      = local.pool_backend_name
  parent_id                 = azurerm_api_management.apim[0].id
  schema_validation_enabled = false

  body = {
    properties = {
      description = "Failover pool: primary (priority 1) -> secondary (priority 2)"
      type        = "Pool"
      pool = {
        services = [
          { id = "/backends/${azapi_resource.backend_primary[0].name}", priority = 1, weight = 1 },
          { id = "/backends/${azapi_resource.backend_secondary[0].name}", priority = 2, weight = 1 },
        ]
      }
    }
  }
}

# -----------------------------------------------------------------------------
# Fireworks backend, used for model-based routing in every policy and as the optional 3.6 failover target.
# -----------------------------------------------------------------------------
resource "azurerm_api_management_named_value" "fireworks_key" {
  count = local.apim_fireworks_enabled && var.fireworks_backend_auth == "api_key" ? 1 : 0

  name                = "fireworks-api-key"
  resource_group_name = azurerm_resource_group.rg.name
  api_management_name = azurerm_api_management.apim[0].name
  display_name        = "fireworks-api-key"
  secret              = true
  value               = azurerm_cognitive_account.fireworks[0].primary_access_key
}

resource "azapi_resource" "backend_fireworks" {
  count = local.apim_fireworks_enabled ? 1 : 0

  type                      = "Microsoft.ApiManagement/service/backends@2024-05-01"
  name                      = local.fireworks_backend_name
  parent_id                 = azurerm_api_management.apim[0].id
  schema_validation_enabled = false

  body = {
    properties = merge({
      description = "Fireworks on Foundry resource (${var.fireworks_location}), OpenAI v1 API"
      # VALIDATE: services.ai.azure.com host for Fireworks deployments (fallback: ai_endpoint_style = "openai")
      url      = "https://${azurerm_cognitive_account.fireworks[0].custom_subdomain_name}.${local.ai_host_suffix}/openai"
      protocol = "http"
    }, local.fireworks_backend_credentials)
  }

  depends_on = [azurerm_api_management_named_value.fireworks_key]
}

resource "azapi_resource" "backend_selfhosted" {
  count = var.deploy_selfhosted_model ? 1 : 0

  type                      = "Microsoft.ApiManagement/service/backends@2024-05-01"
  name                      = local.selfhosted_backend_name
  parent_id                 = azurerm_api_management.apim[0].id
  schema_validation_enabled = false

  body = {
    properties = {
      description = "Ollama on Container Apps (OpenAI-compatible v1 API)"
      url         = var.deploy_selfhosted_model ? trimsuffix(local.selfhosted_base_url, "/v1/") : null
      protocol    = "http"
    }
  }
}

# -----------------------------------------------------------------------------
# API: https://<apim>.azure-api.net/openai/v1/...  (subscription key in the "api-key" header)
# -----------------------------------------------------------------------------
resource "azurerm_api_management_api" "openai" {
  count = local.apim_enabled ? 1 : 0

  name                  = "openai"
  resource_group_name   = azurerm_resource_group.rg.name
  api_management_name   = azurerm_api_management.apim[0].name
  revision              = "1"
  display_name          = "Token Wars - Model gateway"
  description           = "Required OpenAI-compatible v1 gateway for Azure, Fireworks and optional Ollama."
  path                  = "openai"
  protocols             = ["https"]
  service_url           = "https://${azurerm_cognitive_account.ai.custom_subdomain_name}.openai.azure.com/openai"
  subscription_required = true

  subscription_key_parameter_names {
    header = "api-key"
    query  = "api-key"
  }
}

resource "azurerm_api_management_api_operation" "chat_completions" {
  count = local.apim_enabled ? 1 : 0

  operation_id        = "chat-completions"
  api_name            = azurerm_api_management_api.openai[0].name
  api_management_name = azurerm_api_management.apim[0].name
  resource_group_name = azurerm_resource_group.rg.name
  display_name        = "Chat completions"
  method              = "POST"
  url_template        = "/v1/chat/completions"
  description         = "OpenAI v1 chat completions. The deployment name goes into the 'model' field of the body."
}

resource "azurerm_api_management_api_operation" "embeddings" {
  count = local.apim_enabled ? 1 : 0

  operation_id        = "embeddings"
  api_name            = azurerm_api_management_api.openai[0].name
  api_management_name = azurerm_api_management.apim[0].name
  resource_group_name = azurerm_resource_group.rg.name
  display_name        = "Embeddings"
  method              = "POST"
  url_template        = "/v1/embeddings"
  description         = "OpenAI v1 embeddings."
}

resource "azurerm_api_management_api_operation" "models" {
  count = local.apim_enabled ? 1 : 0

  operation_id        = "list-models"
  api_name            = azurerm_api_management_api.openai[0].name
  api_management_name = azurerm_api_management.apim[0].name
  resource_group_name = azurerm_resource_group.rg.name
  display_name        = "List models"
  method              = "GET"
  url_template        = "/v1/models"
  description         = "OpenAI v1 model list (handy for smoke tests)."
}

# The policy (Challenge 3, TODO 3.5) is rendered from a template file; edit it and run `terraform apply` again.
resource "azurerm_api_management_api_policy" "openai" {
  count = local.apim_enabled ? 1 : 0

  api_name            = azurerm_api_management_api.openai[0].name
  api_management_name = azurerm_api_management.apim[0].name
  resource_group_name = azurerm_resource_group.rg.name

  xml_content = templatefile("${path.module}/${var.apim_policy_file}", {
    backend_id            = local.primary_backend_name
    pool_backend_id       = local.failover_backend_name
    pool_models           = [for key in ["premium", "balanced", "economy"] : local.deployment_names[key]]
    tokens_per_minute     = var.tokens_per_minute_per_consumer
    fireworks_backend_id  = local.fireworks_backend_name
    fireworks_auth        = var.fireworks_backend_auth
    fireworks_models      = local.fireworks_enabled ? sort(values(local.fireworks_deployment_names)) : []
    selfhosted_enabled    = var.deploy_selfhosted_model
    selfhosted_model      = var.selfhosted_model
    selfhosted_backend_id = local.selfhosted_backend_name
    failover_model_map    = local.failover_model_map
  })

  lifecycle {
    precondition {
      condition     = !strcontains(var.apim_policy_file, "multiprovider") || local.fireworks_enabled
      error_message = "policies/ai-gateway-multiprovider.xml needs the Fireworks backend: set deploy_fireworks = true (and complete scripts/check-fireworks-prereqs.sh)."
    }
    precondition {
      condition     = !local.fireworks_enabled || length(setintersection(toset(values(local.fireworks_deployment_names)), toset(values(local.deployment_names)))) == 0
      error_message = "Azure and Fireworks deployment names must be distinct so the gateway can route by model."
    }
    precondition {
      condition     = !var.deploy_selfhosted_model || (!contains(values(local.deployment_names), var.selfhosted_model) && (!local.fireworks_enabled || !contains(values(local.fireworks_deployment_names), var.selfhosted_model)))
      error_message = "The Ollama model name must not overlap an Azure or Fireworks deployment name."
    }
    precondition {
      condition     = !strcontains(var.apim_policy_file, "multiprovider") || alltrue([for model in values(local.failover_model_map) : contains(values(local.fireworks_deployment_names), model)])
      error_message = "Every failover_model_map target must name a configured Fireworks deployment."
    }
  }

  depends_on = [
    azapi_resource.backend_primary,
    azapi_resource.backend_secondary,
    azapi_resource.backend_pool,
    azapi_resource.backend_fireworks,
    azapi_resource.backend_selfhosted,
    azurerm_role_assignment.apim_ai,
    azurerm_api_management_logger.appi,
  ]
}

# -----------------------------------------------------------------------------
# One subscription (key) per consumer team -> per-consumer token limits in the policy
# -----------------------------------------------------------------------------
resource "azurerm_api_management_subscription" "consumer" {
  for_each = local.apim_enabled ? toset(var.apim_consumers) : toset([])

  api_management_name = azurerm_api_management.apim[0].name
  resource_group_name = azurerm_resource_group.rg.name
  display_name        = each.value
  api_id              = replace(azurerm_api_management_api.openai[0].id, "/;rev=.*/", "")
  state               = "active"
  allow_tracing       = false
}

# -----------------------------------------------------------------------------
# Observability: App Insights logger + API diagnostic with custom metrics enabled
# (llm-emit-token-metric needs "metrics": true, which azurerm does not expose -> azapi)
# -----------------------------------------------------------------------------
resource "azurerm_api_management_logger" "appi" {
  count = local.apim_enabled ? 1 : 0

  name                = "appinsights"
  api_management_name = azurerm_api_management.apim[0].name
  resource_group_name = azurerm_resource_group.rg.name
  resource_id         = azurerm_application_insights.appi.id

  application_insights {
    connection_string = azurerm_application_insights.appi.connection_string
  }
}

resource "azapi_resource" "api_diagnostic" {
  count = local.apim_enabled ? 1 : 0

  type                      = "Microsoft.ApiManagement/service/apis/diagnostics@2024-05-01"
  name                      = "applicationinsights"
  parent_id                 = "${azurerm_api_management.apim[0].id}/apis/${azurerm_api_management_api.openai[0].name}"
  schema_validation_enabled = false

  body = {
    properties = {
      loggerId                = azurerm_api_management_logger.appi[0].id
      alwaysLog               = "allErrors"
      httpCorrelationProtocol = "W3C"
      verbosity               = "information"
      logClientIp             = true
      metrics                 = true
      sampling = {
        samplingType = "fixed"
        percentage   = 100
      }
    }
  }
}
