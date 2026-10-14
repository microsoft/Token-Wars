# -----------------------------------------------------------------------------
# Generated configuration for the apps (git-ignored):
#   ROOT/.env                        secrets + team settings      (SPEC 2.1)
#   ROOT/shared/config/models.json   model registry for the apps  (SPEC 2.2)
# -----------------------------------------------------------------------------

locals {
  repo_root = abspath("${path.module}/..")

  azure_model_specs = {
    premium   = { deployment = azurerm_cognitive_deployment.premium.name, spec = var.premium_model, type = "chat", via_gateway = true }
    balanced  = { deployment = azurerm_cognitive_deployment.balanced.name, spec = var.balanced_model, type = "chat", via_gateway = true }
    economy   = { deployment = azurerm_cognitive_deployment.economy.name, spec = var.economy_model, type = "chat", via_gateway = true }
    embedding = { deployment = azurerm_cognitive_deployment.embedding.name, spec = var.embedding_model, type = "embedding", via_gateway = true }
    # the judge bypasses the gateway: it is not part of the team score and must not be throttled by the team's token limit
    judge = { deployment = azurerm_cognitive_deployment.judge.name, spec = var.judge_model, type = "chat", via_gateway = false }
  }

  # Deployment names reference the deployment resources, so models.json is written only after they exist.
  open_model_specs = {
    for k, v in {
      open = { deployment = try(azurerm_cognitive_deployment.open[0].name, var.open_model.name), spec = var.open_model, type = "chat", via_gateway = true }
    } : k => v if var.deploy_open_model
  }

  azure_models = {
    for k, m in merge(local.azure_model_specs, local.open_model_specs) : k => {
      deployment           = m.deployment
      base_url             = local.ai_base_url
      api_key_env          = "AZURE_AI_API_KEY"
      pricing_key          = m.spec.pricing_key
      type                 = m.type
      via_gateway          = m.via_gateway
      max_tokens_param     = m.spec.max_tokens_param
      supports_temperature = m.spec.supports_temperature
      extra_body           = m.spec.extra_body
      extra_headers        = m.spec.extra_headers
    }
  }

  selfhosted_models = {
    for k, v in {
      selfhosted = {
        deployment           = var.selfhosted_model
        base_url             = coalesce(local.selfhosted_base_url, "http://localhost:11434/v1/")
        api_key_env          = "SELFHOSTED_API_KEY"
        pricing_key          = "selfhosted"
        type                 = "chat"
        via_gateway          = true
        max_tokens_param     = "max_tokens"
        supports_temperature = true
        extra_body           = {}
        extra_headers        = {}
      }
    } : k => v if var.deploy_selfhosted_model
  }

  # Fireworks deployment names are routed to their backend by every gateway policy.
  fireworks_model_entries = {
    for k, m in local.fireworks_specs : k => {
      deployment           = try(azurerm_cognitive_deployment.fireworks_first[k].name, azurerm_cognitive_deployment.fireworks_rest[k].name, m.deployment_name)
      base_url             = local.fireworks_base_url
      api_key_env          = "FIREWORKS_AI_API_KEY"
      pricing_key          = m.pricing_key
      type                 = "chat"
      via_gateway          = true
      max_tokens_param     = m.max_tokens_param
      supports_temperature = m.supports_temperature
      extra_body           = m.extra_body
      extra_headers        = m.extra_headers
    } if local.fireworks_enabled
  }

  all_models = merge(local.azure_models, local.selfhosted_models, local.fireworks_model_entries)

  gateway = local.apim_enabled ? {
    base_url    = "${try(azurerm_api_management.apim[0].gateway_url, "")}/openai/v1/"
    api_key_env = "APIM_SUBSCRIPTION_KEY"
  } : null

  models_json = <<-EOT
{
  "models": {
${join(",\n", [for k in sort(keys(local.all_models)) : "    ${jsonencode(k)}: ${jsonencode(local.all_models[k])}"])}
  },
  "gateway": ${jsonencode(local.gateway)}
}
EOT

  apim_app_key      = try(azurerm_api_management_subscription.consumer[local.app_consumer].primary_key, "")
  fireworks_api_key = try(azurerm_cognitive_account.fireworks[0].primary_access_key, "")
}

resource "local_file" "models_json" {
  filename        = "${local.repo_root}/shared/config/models.json"
  content         = local.models_json
  file_permission = "0644"
}

resource "local_sensitive_file" "dotenv" {
  filename        = "${local.repo_root}/.env"
  file_permission = "0600"
  content         = <<-EOT
TOKENWARS_TEAM=${var.team_name}
AZURE_AI_API_KEY=${azurerm_cognitive_account.ai.primary_access_key}
APIM_SUBSCRIPTION_KEY=${local.apim_app_key}
SELFHOSTED_API_KEY=ollama
FIREWORKS_AI_API_KEY=${local.fireworks_api_key}
TOKENWARS_LEADERBOARD_URL=${var.leaderboard_url}
TOKENWARS_LEADERBOARD_KEY=${var.leaderboard_key}
TOKENWARS_MOCK=0
EOT
}
