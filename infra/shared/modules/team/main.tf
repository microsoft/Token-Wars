# -----------------------------------------------------------------------------
# One team in shared-infrastructure mode: RG + APIM (+ App Insights) in front of the coach's model resources.
# Mirrors infra/apim.tf so the Challenge 3 policy templates render unchanged.
# -----------------------------------------------------------------------------

locals {
  primary_backend_name    = "aoai-primary"
  secondary_backend_name  = "aoai-secondary"
  pool_backend_name       = "aoai-pool"
  fireworks_backend_name  = "fireworks"
  selfhosted_backend_name = "selfhosted"

  secondary_enabled  = var.secondary != null
  fireworks_enabled  = var.fireworks != null
  selfhosted_enabled = var.selfhosted != null

  failover_backend_name = local.secondary_enabled ? local.pool_backend_name : local.primary_backend_name
  app_consumer          = contains(var.apim_consumers, "support-squad") ? "support-squad" : var.apim_consumers[0]
  fireworks_auth        = local.fireworks_enabled ? var.fireworks.auth : "api_key"
  fireworks_models      = local.fireworks_enabled ? var.fireworks.models : []
  failover_model_map    = local.fireworks_enabled ? var.fireworks.failover_mapping : {}

  role_accounts = merge(
    { primary = var.primary.id },
    local.secondary_enabled ? { secondary = var.secondary.id } : {},
    local.fireworks_enabled ? { fireworks = var.fireworks.id } : {}
  )
  # Same keyless data-plane roles as infra/apim.tf, scoped to the coach's Foundry resources.
  roles = ["Cognitive Services OpenAI User", "Cognitive Services User"]
  role_assignments = {
    for pair in setproduct(keys(local.role_accounts), local.roles) :
    "${pair[0]}|${pair[1]}" => { scope = local.role_accounts[pair[0]], role = pair[1] }
  }

  fireworks_backend_credentials = local.fireworks_auth == "api_key" ? {
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

  primary_url = "https://${var.primary.subdomain}.openai.azure.com/openai"
}

resource "azurerm_resource_group" "rg" {
  name     = "rg-tw-shared-${var.team}-${var.suffix}"
  location = var.location
  tags     = var.tags
}

# Per-team Application Insights in the coach's Log Analytics workspace: the coach can tell teams apart
# (_ResourceId) even though every team APIM uses the same subscription names in the `team` dimension.
resource "azurerm_application_insights" "appi" {
  name                = "appi-tws-${var.team}-${var.suffix}"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  workspace_id        = var.log_analytics_workspace_id
  application_type    = "web"
  tags                = var.tags
}

resource "azurerm_api_management" "apim" {
  name                = "apim-tws-${var.team}-${var.suffix}"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  publisher_name      = "ByteCart Token Wars"
  publisher_email     = var.apim_publisher_email
  sku_name            = var.apim_sku

  identity {
    type = "SystemAssigned"
  }

  tags = var.tags
}

resource "azurerm_role_assignment" "apim_ai" {
  for_each = local.role_assignments

  scope                = each.value.scope
  role_definition_name = each.value.role
  principal_id         = azurerm_api_management.apim.identity[0].principal_id
  principal_type       = "ServicePrincipal"
}

# -----------------------------------------------------------------------------
# Backends (same names as infra/apim.tf, so the policy templates work unchanged)
# -----------------------------------------------------------------------------
resource "azapi_resource" "backend_primary" {
  type                      = "Microsoft.ApiManagement/service/backends@2024-05-01"
  name                      = local.primary_backend_name
  parent_id                 = azurerm_api_management.apim.id
  schema_validation_enabled = false

  body = {
    properties = {
      description    = "Coach primary Foundry resource (shared)"
      url            = local.primary_url
      protocol       = "http"
      circuitBreaker = local.circuit_breaker
    }
  }
}

resource "azapi_resource" "backend_secondary" {
  count = local.secondary_enabled ? 1 : 0

  type                      = "Microsoft.ApiManagement/service/backends@2024-05-01"
  name                      = local.secondary_backend_name
  parent_id                 = azurerm_api_management.apim.id
  schema_validation_enabled = false

  body = {
    properties = {
      description    = "Coach secondary Foundry resource (shared)"
      url            = "https://${var.secondary.subdomain}.openai.azure.com/openai"
      protocol       = "http"
      circuitBreaker = local.circuit_breaker
    }
  }
}

resource "azapi_resource" "backend_pool" {
  count = local.secondary_enabled ? 1 : 0

  type                      = "Microsoft.ApiManagement/service/backends@2024-05-01"
  name                      = local.pool_backend_name
  parent_id                 = azurerm_api_management.apim.id
  schema_validation_enabled = false

  body = {
    properties = {
      description = "Failover pool: primary (priority 1) -> secondary (priority 2)"
      type        = "Pool"
      pool = {
        services = [
          { id = "/backends/${azapi_resource.backend_primary.name}", priority = 1, weight = 1 },
          { id = "/backends/${azapi_resource.backend_secondary[0].name}", priority = 2, weight = 1 },
        ]
      }
    }
  }
}

resource "azurerm_api_management_named_value" "fireworks_key" {
  count = local.fireworks_enabled && local.fireworks_auth == "api_key" ? 1 : 0

  name                = "fireworks-api-key"
  resource_group_name = azurerm_resource_group.rg.name
  api_management_name = azurerm_api_management.apim.name
  display_name        = "fireworks-api-key"
  secret              = true
  value               = var.fireworks.api_key
}

resource "azapi_resource" "backend_fireworks" {
  count = local.fireworks_enabled ? 1 : 0

  type                      = "Microsoft.ApiManagement/service/backends@2024-05-01"
  name                      = local.fireworks_backend_name
  parent_id                 = azurerm_api_management.apim.id
  schema_validation_enabled = false

  body = {
    properties = merge({
      description = "Coach Fireworks on Foundry resource (shared), OpenAI v1 API"
      url         = var.fireworks.backend_url
      protocol    = "http"
    }, local.fireworks_backend_credentials)
  }

  depends_on = [azurerm_api_management_named_value.fireworks_key]
}

resource "azapi_resource" "backend_selfhosted" {
  count = local.selfhosted_enabled ? 1 : 0

  type                      = "Microsoft.ApiManagement/service/backends@2024-05-01"
  name                      = local.selfhosted_backend_name
  parent_id                 = azurerm_api_management.apim.id
  schema_validation_enabled = false

  body = {
    properties = {
      description = "Coach Ollama on Container Apps (shared, OpenAI-compatible v1 API)"
      url         = trimsuffix(var.selfhosted.base_url, "/v1/")
      protocol    = "http"
    }
  }
}

# -----------------------------------------------------------------------------
# Model API (Challenge 3 policy) - identical contract to infra/apim.tf
# -----------------------------------------------------------------------------
resource "azurerm_api_management_api" "openai" {
  name                  = "openai"
  resource_group_name   = azurerm_resource_group.rg.name
  api_management_name   = azurerm_api_management.apim.name
  revision              = "1"
  display_name          = "Token Wars - Model gateway"
  description           = "Required OpenAI-compatible v1 gateway (shared mode) for Azure, Fireworks, Ollama and custom models."
  path                  = "openai"
  protocols             = ["https"]
  service_url           = local.primary_url
  subscription_required = true

  subscription_key_parameter_names {
    header = "api-key"
    query  = "api-key"
  }
}

resource "azurerm_api_management_api_operation" "chat_completions" {
  operation_id        = "chat-completions"
  api_name            = azurerm_api_management_api.openai.name
  api_management_name = azurerm_api_management.apim.name
  resource_group_name = azurerm_resource_group.rg.name
  display_name        = "Chat completions"
  method              = "POST"
  url_template        = "/v1/chat/completions"
  description         = "OpenAI v1 chat completions. The deployment name goes into the 'model' field of the body."
}

resource "azurerm_api_management_api_operation" "embeddings" {
  operation_id        = "embeddings"
  api_name            = azurerm_api_management_api.openai.name
  api_management_name = azurerm_api_management.apim.name
  resource_group_name = azurerm_resource_group.rg.name
  display_name        = "Embeddings"
  method              = "POST"
  url_template        = "/v1/embeddings"
  description         = "OpenAI v1 embeddings."
}

resource "azurerm_api_management_api_operation" "models" {
  operation_id        = "list-models"
  api_name            = azurerm_api_management_api.openai.name
  api_management_name = azurerm_api_management.apim.name
  resource_group_name = azurerm_resource_group.rg.name
  display_name        = "List models"
  method              = "GET"
  url_template        = "/v1/models"
  description         = "OpenAI v1 model list (handy for smoke tests)."
}

# Rendered from the team's policy template (set_policy.py). Same variables as infra/apim.tf.
resource "azurerm_api_management_api_policy" "openai" {
  api_name            = azurerm_api_management_api.openai.name
  api_management_name = azurerm_api_management.apim.name
  resource_group_name = azurerm_resource_group.rg.name

  xml_content = templatefile(var.policy_file, {
    backend_id            = local.primary_backend_name
    pool_backend_id       = local.failover_backend_name
    pool_models           = var.pool_models
    tokens_per_minute     = var.tokens_per_minute
    fireworks_backend_id  = local.fireworks_backend_name
    fireworks_auth        = local.fireworks_auth
    fireworks_models      = local.fireworks_models
    selfhosted_enabled    = local.selfhosted_enabled
    selfhosted_model      = local.selfhosted_enabled ? var.selfhosted.model : ""
    selfhosted_backend_id = local.selfhosted_backend_name
    failover_model_map    = local.failover_model_map
  })

  lifecycle {
    precondition {
      condition     = !strcontains(var.policy_file, "multiprovider") || local.fireworks_enabled
      error_message = "The multiprovider policy needs the coach's Fireworks resource (deploy_fireworks = true in infra/)."
    }
    precondition {
      condition     = !strcontains(var.policy_file, "multiprovider") || alltrue([for model in values(local.failover_model_map) : contains(local.fireworks_models, model)])
      error_message = "Every failover_model_map target must name a Fireworks deployment of the coach stack."
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
# Judge API: coach-owned, judge deployment only, separate key, not affected by the team's Challenge 3 policy.
#   https://<team-apim>.azure-api.net/judge/v1/chat/completions
# -----------------------------------------------------------------------------
resource "azurerm_api_management_api" "judge" {
  name                  = "judge"
  resource_group_name   = azurerm_resource_group.rg.name
  api_management_name   = azurerm_api_management.apim.name
  revision              = "1"
  display_name          = "Token Wars - Judge (coach-managed)"
  description           = "Independent judge. Serves only the judge deployment; excluded from the team token budget."
  path                  = "judge"
  protocols             = ["https"]
  service_url           = local.primary_url
  subscription_required = true

  subscription_key_parameter_names {
    header = "api-key"
    query  = "api-key"
  }
}

resource "azurerm_api_management_api_operation" "judge_chat" {
  operation_id        = "chat-completions"
  api_name            = azurerm_api_management_api.judge.name
  api_management_name = azurerm_api_management.apim.name
  resource_group_name = azurerm_resource_group.rg.name
  display_name        = "Judge chat completions"
  method              = "POST"
  url_template        = "/v1/chat/completions"
  description         = "OpenAI v1 chat completions for the judge deployment only."
}

resource "azurerm_api_management_api_policy" "judge" {
  api_name            = azurerm_api_management_api.judge.name
  api_management_name = azurerm_api_management.apim.name
  resource_group_name = azurerm_resource_group.rg.name

  xml_content = templatefile(var.judge_policy_file, {
    backend_id       = local.primary_backend_name
    judge_deployment = var.judge_deployment
  })

  depends_on = [
    azapi_resource.backend_primary,
    azurerm_role_assignment.apim_ai,
  ]
}

# -----------------------------------------------------------------------------
# Subscriptions: one per consumer on the model API (Challenge 3.5 counters) + one judge-only key
# -----------------------------------------------------------------------------
resource "azurerm_api_management_subscription" "consumer" {
  for_each = toset(var.apim_consumers)

  api_management_name = azurerm_api_management.apim.name
  resource_group_name = azurerm_resource_group.rg.name
  display_name        = each.value
  api_id              = replace(azurerm_api_management_api.openai.id, "/;rev=.*/", "")
  state               = "active"
  allow_tracing       = false
}

resource "azurerm_api_management_subscription" "judge" {
  api_management_name = azurerm_api_management.apim.name
  resource_group_name = azurerm_resource_group.rg.name
  display_name        = "judge"
  api_id              = replace(azurerm_api_management_api.judge.id, "/;rev=.*/", "")
  state               = "active"
  allow_tracing       = false
}

# -----------------------------------------------------------------------------
# Observability (llm-emit-token-metric needs "metrics": true -> azapi)
# -----------------------------------------------------------------------------
resource "azurerm_api_management_logger" "appi" {
  name                = "appinsights"
  api_management_name = azurerm_api_management.apim.name
  resource_group_name = azurerm_resource_group.rg.name
  resource_id         = azurerm_application_insights.appi.id

  application_insights {
    connection_string = azurerm_application_insights.appi.connection_string
  }
}

resource "azapi_resource" "api_diagnostic" {
  type                      = "Microsoft.ApiManagement/service/apis/diagnostics@2024-05-01"
  name                      = "applicationinsights"
  parent_id                 = "${azurerm_api_management.apim.id}/apis/${azurerm_api_management_api.openai.name}"
  schema_validation_enabled = false

  body = {
    properties = {
      loggerId                = azurerm_api_management_logger.appi.id
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

# -----------------------------------------------------------------------------
# Team files (hand them to the team: .env -> repo root, models.json -> shared/config/models.json)
# -----------------------------------------------------------------------------
locals {
  gateway_root = azurerm_api_management.apim.gateway_url
  gateway = {
    base_url    = "${local.gateway_root}/openai/v1/"
    api_key_env = "APIM_SUBSCRIPTION_KEY"
  }
  # Every non-judge model is served through this team's gateway; the coach backend URLs are not exposed.
  models = merge({ for k, m in var.models : k => merge(m, { base_url = local.gateway.base_url }) }, {
    judge = merge(var.judge_model, {
      base_url    = "${local.gateway_root}/judge/v1/"
      api_key_env = "JUDGE_API_KEY"
      via_gateway = false
    })
  })

  models_json = <<-EOT
{
  "models": {
${join(",\n", [for k in sort(keys(local.models)) : "    ${jsonencode(k)}: ${jsonencode(local.models[k])}"])}
  },
  "gateway": ${jsonencode(local.gateway)}
}
EOT

  consumer_key_lines = join("\n", [
    for c in sort(var.apim_consumers) :
    "APIM_KEY_${upper(replace(c, "-", "_"))}=${azurerm_api_management_subscription.consumer[c].primary_key}"
  ])
}

resource "local_file" "models_json" {
  filename        = "${var.output_dir}/models.json"
  content         = local.models_json
  file_permission = "0644"
}

resource "local_sensitive_file" "dotenv" {
  filename        = "${var.output_dir}/.env"
  file_permission = "0600"
  content         = <<-EOT
# Token Wars - shared-infrastructure mode (generated by infra/shared for team ${var.team}). Do not commit.
TOKENWARS_TEAM=${var.team}
APIM_SUBSCRIPTION_KEY=${azurerm_api_management_subscription.consumer[local.app_consumer].primary_key}
JUDGE_API_KEY=${azurerm_api_management_subscription.judge.primary_key}
APIM_GATEWAY_URL=${local.gateway.base_url}
SELFHOSTED_API_KEY=ollama
TOKENWARS_LEADERBOARD_URL=${var.leaderboard_url}
TOKENWARS_LEADERBOARD_KEY=${var.leaderboard_key}
TOKENWARS_MOCK=0
# One key per APIM consumer (Challenge 3.5: per-consumer token limit tests)
${local.consumer_key_lines}
EOT
}
