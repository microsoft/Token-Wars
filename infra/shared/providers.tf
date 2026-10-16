terraform {
  required_version = ">= 1.6.0"

  # Same provider versions as the coach stack in infra/.
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 5.8"
    }
    azapi = {
      source  = "Azure/azapi"
      version = "~> 2.2"
    }
    local = {
      source  = "hashicorp/local"
      version = "~> 2.5"
    }
  }
}

# The team resource groups are created in the coach subscription. deploy_teams.py sets ARM_SUBSCRIPTION_ID from the
# coach state; set var.subscription_id or ARM_SUBSCRIPTION_ID yourself when you run terraform directly.
provider "azurerm" {
  subscription_id = var.subscription_id

  resource_providers_to_register = [
    "Microsoft.ApiManagement",
    "Microsoft.OperationalInsights",
    "Microsoft.Insights",
  ]

  features {
    resource_group {
      prevent_deletion_if_contains_resources = false
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
