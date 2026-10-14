output "resource_group_name" {
  description = "Resource group containing everything (delete it or run terraform destroy after the event)."
  value       = azurerm_resource_group.rg.name
}

output "ai_account_name" {
  description = "Primary Microsoft Foundry resource (Microsoft.CognitiveServices/accounts, kind AIServices)."
  value       = azurerm_cognitive_account.ai.name
}

output "ai_base_url" {
  description = "OpenAI-compatible v1 base URL used by the apps (host chosen by ai_endpoint_style)."
  value       = local.ai_base_url
}

output "ai_endpoint_style" {
  description = "\"foundry\" (services.ai.azure.com) or \"openai\" (openai.azure.com)."
  value       = var.ai_endpoint_style
}

output "foundry_project_name" {
  description = "Foundry project (open it in the new Foundry portal at https://ai.azure.com)."
  value       = azurerm_cognitive_account_project.ai.name
}

# VALIDATE: key name of the project endpoint in the exported "endpoints" map ("AI Foundry API" in the ARM response).
output "foundry_project_endpoint" {
  description = "Foundry project endpoint (Foundry SDK / Agents / Responses API), from the project's endpoints map."
  value = try(
    azurerm_cognitive_account_project.ai.endpoints["AI Foundry API"],
    values(azurerm_cognitive_account_project.ai.endpoints)[0],
    "https://${azurerm_cognitive_account.ai.custom_subdomain_name}.services.ai.azure.com/api/projects/${azurerm_cognitive_account_project.ai.name}"
  )
}

output "foundry_project_endpoints" {
  description = "All endpoints exported by the Foundry project."
  value       = azurerm_cognitive_account_project.ai.endpoints
}

output "foundry_portal_hint" {
  description = "How to find your models in the new Foundry portal."
  value       = <<-EOT
    Open https://ai.azure.com (new Foundry portal - make sure the "New Foundry" toggle is on), sign in with the account
    that owns subscription ${data.azurerm_client_config.current.subscription_id}, and select the project
    "${local.foundry_project_display_name}" (${azurerm_cognitive_account_project.ai.name}) on resource ${azurerm_cognitive_account.ai.name}.
    Models / deployments, the playground, quotas and monitoring are under that project.%{if local.fireworks_enabled}
    Fireworks models live in a second project on resource ${local.fireworks_account_name} (${var.fireworks_location}).%{endif}
  EOT
}

output "deployments" {
  description = "Model key -> deployment name."
  value       = { for k, m in local.all_models : k => m.deployment }
}

output "apim_gateway_url" {
  description = "Required AI gateway base URL for all inference except the judge."
  value       = local.apim_enabled ? local.gateway.base_url : null
}

output "apim_name" {
  description = "API Management instance name."
  value       = try(azurerm_api_management.apim[0].name, null)
}

output "apim_policy_file" {
  description = "Policy template currently applied to the 'openai' API."
  value       = local.apim_enabled ? var.apim_policy_file : null
}

output "apim_failover_backend" {
  description = "Backend id passed to the policy as pool_backend_id (the pool if a secondary region is deployed)."
  value       = local.apim_enabled ? local.failover_backend_name : null
}

output "apim_consumer_keys" {
  description = "APIM subscription key per consumer (terraform output -json apim_consumer_keys)."
  value       = { for k, s in azurerm_api_management_subscription.consumer : k => s.primary_key }
  sensitive   = true
}

output "fireworks_account_name" {
  description = "Fireworks on Foundry resource (null if deploy_fireworks = false)."
  value       = try(azurerm_cognitive_account.fireworks[0].name, null)
}

output "fireworks_base_url" {
  description = "OpenAI-compatible v1 base URL of the Fireworks resource (models.json keys fw_*)."
  value       = local.fireworks_base_url
}

output "fireworks_deployments" {
  description = "models.json key -> Fireworks deployment name."
  value       = local.fireworks_enabled ? local.fireworks_deployment_names : {}
}

output "fireworks_project_endpoint" {
  description = "Foundry project endpoint of the Fireworks resource."
  value = local.fireworks_enabled ? try(
    azurerm_cognitive_account_project.fireworks[0].endpoints["AI Foundry API"],
    values(azurerm_cognitive_account_project.fireworks[0].endpoints)[0],
    "https://${local.fireworks_account_name}.services.ai.azure.com/api/projects/${var.foundry_project_name}"
  ) : null
}

output "apim_failover_model_map" {
  description = "Model rewrite for Challenge 3.6 failover (null unless Fireworks is deployed; APIM is required)."
  value       = local.apim_fireworks_enabled ? local.failover_model_map : null
}

output "selfhosted_base_url" {
  description = "Ollama OpenAI-compatible base URL (null if not deployed)."
  value       = local.selfhosted_base_url
}

output "application_insights_name" {
  description = "Application Insights receiving APIM logs and token metrics (namespace 'tokenwars')."
  value       = azurerm_application_insights.appi.name
}

output "generated_files" {
  description = "Files written for the apps."
  value = {
    dotenv      = local_sensitive_file.dotenv.filename
    models_json = local_file.models_json.filename
  }
}

output "next_steps" {
  description = "What to do next."
  value       = <<-EOT
    Generated ${local_file.models_json.filename} and ${local_sensitive_file.dotenv.filename}.
    Python : cd ../python/starter && python -m tokenwars doctor && python -m tokenwars run --limit 20
    .NET   : cd ../dotnet/starter/TokenWars && dotnet run -- doctor && dotnet run -- run --limit 20
    Portal : https://ai.azure.com -> project ${azurerm_cognitive_account_project.ai.name}%{if local.fireworks_enabled}
    Arena  : python -m tokenwars compare --models balanced,premium,fw   (Challenge 2.4)%{endif}
    Cleanup: terraform destroy
  EOT
}
