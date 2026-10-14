terraform {
  required_version = ">= 1.6.0"

  required_providers {
    # azurerm 5.x (5.8.0+ as of Oct 2026). Checked against the 5.0 upgrade guide:
    #   * no Resource Provider is registered by default any more  -> resource_providers_to_register below
    #   * azurerm_container_app_environment.logs_destination must be set explicitly -> selfhosted.tf
    #   * no breaking change for cognitive_account / cognitive_deployment / api_management* /
    #     application_insights / log_analytics_workspace arguments used here; features{} sub-blocks unchanged.
    # azurerm_cognitive_account_project (Foundry project) exists since azurerm 4.55.0; the 4.x line ended with 4.81.0.
    # Fallback if you must stay on 4.x: version = "~> 4.81" and remove resource_providers_to_register + logs_destination.
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 5.8"
    }
    # azapi is only used for what azurerm cannot express: APIM backend pools / circuit breakers / backend credentials
    # and the API diagnostic "metrics" switch required by llm-emit-token-metric.
    azapi = {
      source  = "Azure/azapi"
      version = "~> 2.2"
    }
    local = {
      source  = "hashicorp/local"
      version = "~> 2.5"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "azurerm" {
  # azurerm needs a subscription: set var.subscription_id or export ARM_SUBSCRIPTION_ID
  # (e.g. export ARM_SUBSCRIPTION_ID=$(az account show --query id -o tsv)).
  subscription_id = var.subscription_id

  # azurerm 5.x registers NO Resource Providers by default (4.x registered ~60). Fresh team subscriptions need these.
  # Registering requires Owner/Contributor on the subscription (teams use their own subscription).
  resource_providers_to_register = [
    "Microsoft.CognitiveServices",
    "Microsoft.ApiManagement",
    "Microsoft.OperationalInsights",
    "Microsoft.Insights",
    "Microsoft.App",
  ]

  features {
    resource_group {
      prevent_deletion_if_contains_resources = false
    }
    cognitive_account {
      purge_soft_delete_on_destroy = true
    }
    api_management {
      purge_soft_delete_on_destroy = true
      recover_soft_deleted         = true
    }
  }
}

provider "azapi" {
  subscription_id = var.subscription_id
}
