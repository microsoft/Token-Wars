output "resource_group_name" {
  value = azurerm_resource_group.rg.name
}

output "apim_name" {
  value = azurerm_api_management.apim.name
}

output "gateway_url" {
  value = local.gateway.base_url
}

output "judge_url" {
  value = "${local.gateway_root}/judge/v1/"
}

output "dotenv_file" {
  value = local_sensitive_file.dotenv.filename
}

output "models_json_file" {
  value = local_file.models_json.filename
}
