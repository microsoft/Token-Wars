mock_provider "azurerm" {}
mock_provider "azapi" {}
mock_provider "local" {}
mock_provider "random" {}

variables {
  team_name               = "decision-tests"
  subscription_id         = "00000000-0000-0000-0000-000000000001"
  deploy_open_model       = false
  deploy_fireworks        = false
  deploy_selfhosted_model = false
  deploy_secondary_region = false
}

run "decision_is_required_and_gateway_routed" {
  command = plan

  assert {
    condition     = azurerm_cognitive_deployment.decision.name == "decision" && local.all_models.decision.deployment == "decision"
    error_message = "The deployment and registry must use the neutral name 'decision', not the reserved publisher name."
  }
  assert {
    condition     = azurerm_cognitive_deployment.decision.model[0].name == "Microsoft-Decision-1" && azurerm_cognitive_deployment.decision.model[0].format == "Microsoft"
    error_message = "Decision-1 must deploy even when optional model deployments are disabled."
  }
  assert {
    condition     = azurerm_cognitive_deployment.decision.model[0].version == "1" && azurerm_cognitive_deployment.decision.sku[0].name == var.decision_model.sku && contains(["GlobalStandard", "DataZoneStandard"], var.decision_model.sku)
    error_message = "The required Decision deployment must use the documented model version and pay-per-token SKU."
  }
  assert {
    condition     = local.all_models.decision.type == "decision" && local.all_models.decision.via_gateway && local.all_models.decision.pricing_key == "microsoft-decision-1"
    error_message = "The generated registry must include a priced, gateway-only decision entry."
  }
  assert {
    condition     = azurerm_api_management_api_operation.decisions[0].url_template == "/v1/decisions"
    error_message = "APIM must expose the operation used by both application tracks."
  }
  assert {
    condition     = strcontains(azurerm_api_management_api_policy.openai[0].xml_content, "rewrite-uri template=\"/systemone\"")
    error_message = "The default gateway policy must rewrite Decision calls to the native provider operation."
  }
  assert {
    condition     = strcontains(azurerm_api_management_api_policy.openai[0].xml_content, "context.Variables[&quot;tw-model&quot;] != &quot;decision&quot;")
    error_message = "APIM must validate the deployment name 'decision', not the catalog model name."
  }
}

run "decision_rejects_lowercase_catalog_name" {
  command = plan

  variables {
    decision_model = { name = "microsoft-decision-1" }
  }

  expect_failures = [var.decision_model]
}

run "decision_rejects_wrong_model_format" {
  command = plan

  variables {
    decision_model = { format = "OpenAI" }
  }

  expect_failures = [var.decision_model]
}
