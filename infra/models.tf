# Model deployments on the Foundry resource (deployments stay on the resource, not on the project).
# Deployments on the same resource are created one after another (depends_on chain): parallel deployment
# operations on one resource frequently fail with "RequestConflict / another operation in progress".
# Fireworks deployments: fireworks.tf

locals {
  deployment_names = {
    premium   = var.premium_model.name
    balanced  = var.balanced_model.name
    economy   = var.economy_model.name
    judge     = "judge"
    embedding = var.embedding_model.name
    open      = var.open_model.name
  }
}

resource "azurerm_cognitive_deployment" "premium" {
  name                 = local.deployment_names.premium
  cognitive_account_id = azurerm_cognitive_account.ai.id

  model {
    format  = var.premium_model.format
    name    = var.premium_model.name
    version = var.premium_model.version
  }

  sku {
    name     = var.premium_model.sku
    capacity = var.premium_model.capacity
  }
}

resource "azurerm_cognitive_deployment" "balanced" {
  name                 = local.deployment_names.balanced
  cognitive_account_id = azurerm_cognitive_account.ai.id

  model {
    format  = var.balanced_model.format
    name    = var.balanced_model.name
    version = var.balanced_model.version
  }

  sku {
    name     = var.balanced_model.sku
    capacity = var.balanced_model.capacity
  }

  depends_on = [azurerm_cognitive_deployment.premium]
}

resource "azurerm_cognitive_deployment" "economy" {
  name                 = local.deployment_names.economy
  cognitive_account_id = azurerm_cognitive_account.ai.id

  model {
    format  = var.economy_model.format
    name    = var.economy_model.name
    version = var.economy_model.version
  }

  sku {
    name     = var.economy_model.sku
    capacity = var.economy_model.capacity
  }

  depends_on = [azurerm_cognitive_deployment.balanced]
}

resource "azurerm_cognitive_deployment" "judge" {
  name                 = local.deployment_names.judge
  cognitive_account_id = azurerm_cognitive_account.ai.id

  model {
    format  = var.judge_model.format
    name    = var.judge_model.name
    version = var.judge_model.version
  }

  sku {
    name     = var.judge_model.sku
    capacity = var.judge_model.capacity
  }

  depends_on = [azurerm_cognitive_deployment.economy]
}

resource "azurerm_cognitive_deployment" "embedding" {
  name                 = local.deployment_names.embedding
  cognitive_account_id = azurerm_cognitive_account.ai.id

  model {
    format  = var.embedding_model.format
    name    = var.embedding_model.name
    version = var.embedding_model.version
  }

  sku {
    name     = var.embedding_model.sku
    capacity = var.embedding_model.capacity
  }

  depends_on = [azurerm_cognitive_deployment.judge]
}

resource "azurerm_cognitive_deployment" "open" {
  count = var.deploy_open_model ? 1 : 0

  name                 = local.deployment_names.open
  cognitive_account_id = azurerm_cognitive_account.ai.id

  model {
    format  = var.open_model.format
    name    = var.open_model.name
    version = var.open_model.version
  }

  sku {
    name     = var.open_model.sku
    capacity = var.open_model.capacity
  }

  depends_on = [azurerm_cognitive_deployment.embedding]
}

# -----------------------------------------------------------------------------
# Secondary region: same deployment NAMES as the primary so the APIM backend pool can fail over transparently.
# Only the chat tiers are replicated (the judge, embedding and open model stay in the primary region).
# -----------------------------------------------------------------------------
resource "azurerm_cognitive_deployment" "premium_secondary" {
  count = local.secondary_enabled ? 1 : 0

  name                 = local.deployment_names.premium
  cognitive_account_id = azurerm_cognitive_account.ai_secondary[0].id

  model {
    format  = var.premium_model.format
    name    = var.premium_model.name
    version = var.premium_model.version
  }

  sku {
    name     = var.premium_model.sku
    capacity = var.secondary_capacity
  }
}

resource "azurerm_cognitive_deployment" "balanced_secondary" {
  count = local.secondary_enabled ? 1 : 0

  name                 = local.deployment_names.balanced
  cognitive_account_id = azurerm_cognitive_account.ai_secondary[0].id

  model {
    format  = var.balanced_model.format
    name    = var.balanced_model.name
    version = var.balanced_model.version
  }

  sku {
    name     = var.balanced_model.sku
    capacity = var.secondary_capacity
  }

  depends_on = [azurerm_cognitive_deployment.premium_secondary]
}

resource "azurerm_cognitive_deployment" "economy_secondary" {
  count = local.secondary_enabled ? 1 : 0

  name                 = local.deployment_names.economy
  cognitive_account_id = azurerm_cognitive_account.ai_secondary[0].id

  model {
    format  = var.economy_model.format
    name    = var.economy_model.name
    version = var.economy_model.version
  }

  sku {
    name     = var.economy_model.sku
    capacity = var.secondary_capacity
  }

  depends_on = [azurerm_cognitive_deployment.balanced_secondary]
}
