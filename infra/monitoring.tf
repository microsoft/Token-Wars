resource "azurerm_log_analytics_workspace" "law" {
  name                = "log-tw-${local.team}-${local.suffix}"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = local.tags
}

resource "azurerm_application_insights" "appi" {
  name                = "appi-tw-${local.team}-${local.suffix}"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  workspace_id        = azurerm_log_analytics_workspace.law.id
  application_type    = "web"
  tags                = local.tags
}

# Token metrics emitted by the llm-emit-token-metric policy land in Application Insights > Metrics,
# namespace "tokenwars" (custom metrics). To split charts by the policy's dimensions, enable
# "Application Insights > Usage and estimated costs > Custom metrics > With dimensions" in the portal.
