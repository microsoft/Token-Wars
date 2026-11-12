resource "random_id" "suffix" {
  byte_length = 4
}

locals {
  suffix = random_id.suffix.hex
  tags   = { project = "tokenwars", component = "leaderboard", SecurityControl = "Ignore" }
}

resource "azurerm_resource_group" "leaderboard" {
  name     = "rg-tokenwars-leaderboard"
  location = var.location
  tags     = local.tags
}

resource "azurerm_storage_account" "leaderboard" {
  name                            = "sttwlb${local.suffix}"
  resource_group_name             = azurerm_resource_group.leaderboard.name
  location                        = azurerm_resource_group.leaderboard.location
  account_tier                    = "Standard"
  account_replication_type        = "ZRS"
  min_tls_version                 = "TLS1_2"
  https_traffic_only_enabled      = true
  shared_access_key_enabled       = false
  default_to_oauth_authentication = true
  allow_nested_items_to_be_public = false
  tags                            = local.tags
}

resource "azurerm_user_assigned_identity" "leaderboard" {
  name                = "id-${var.name}-${local.suffix}"
  resource_group_name = azurerm_resource_group.leaderboard.name
  location            = azurerm_resource_group.leaderboard.location
  tags                = local.tags
}

resource "azurerm_role_assignment" "blob" {
  scope                = azurerm_storage_account.leaderboard.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.leaderboard.principal_id
}

data "azurerm_client_config" "current" {}

locals {
  build_image  = var.container_image == null
  source_dir   = abspath("${path.module}/../../leaderboard")
  source_hash  = sha1(join("", [for f in ["Dockerfile", "requirements.txt", "server.py", "storage.py"] : filesha1("${local.source_dir}/${f}")]))
  built_image  = local.build_image ? "${azurerm_container_registry.leaderboard[0].login_server}/tokenwars-leaderboard:${substr(local.source_hash, 0, 12)}" : null
  image        = coalesce(var.container_image, local.built_image)
  registry_id  = local.build_image ? azurerm_container_registry.leaderboard[0].id : var.container_registry_id
  use_registry = local.build_image || var.container_registry_id != null
}

resource "azurerm_container_registry" "leaderboard" {
  count = local.build_image ? 1 : 0

  name                = "acrtwlb${local.suffix}"
  resource_group_name = azurerm_resource_group.leaderboard.name
  location            = azurerm_resource_group.leaderboard.location
  sku                 = "Basic"
  admin_enabled       = false
  tags                = local.tags
}

resource "azurerm_role_assignment" "image" {
  count = local.use_registry ? 1 : 0

  scope                = local.registry_id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.leaderboard.principal_id
}

# Builds in the cloud (no local Docker). Requires `az login`; reruns when the app source changes.
resource "terraform_data" "image_build" {
  count = local.build_image ? 1 : 0

  triggers_replace = [local.built_image, azurerm_container_registry.leaderboard[0].id]

  provisioner "local-exec" {
    command = "az acr build --subscription ${data.azurerm_client_config.current.subscription_id} --registry ${azurerm_container_registry.leaderboard[0].name} --image ${trimprefix(local.built_image, "${azurerm_container_registry.leaderboard[0].login_server}/")} \"${local.source_dir}\""
  }

  depends_on = [azurerm_role_assignment.image]
}

resource "azurerm_log_analytics_workspace" "leaderboard" {
  name                = "law-${var.name}-${local.suffix}"
  resource_group_name = azurerm_resource_group.leaderboard.name
  location            = azurerm_resource_group.leaderboard.location
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = local.tags
}

resource "azurerm_container_app_environment" "leaderboard" {
  name                       = "cae-${var.name}-${local.suffix}"
  resource_group_name        = azurerm_resource_group.leaderboard.name
  location                   = azurerm_resource_group.leaderboard.location
  logs_destination           = "log-analytics"
  log_analytics_workspace_id = azurerm_log_analytics_workspace.leaderboard.id
  tags                       = local.tags

  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }
}

resource "azurerm_container_app" "leaderboard" {
  name                         = "${var.name}-${local.suffix}"
  container_app_environment_id = azurerm_container_app_environment.leaderboard.id
  resource_group_name          = azurerm_resource_group.leaderboard.name
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.leaderboard.id]
  }

  dynamic "registry" {
    for_each = local.use_registry ? [1] : []
    content {
      server   = split("/", local.image)[0]
      identity = azurerm_user_assigned_identity.leaderboard.id
    }
  }

  secret {
    name  = "admin-key"
    value = var.admin_key
  }

  dynamic "secret" {
    for_each = var.initial_leaderboard_key == "" ? [] : ["initial-key"]
    content {
      name  = secret.value
      value = var.initial_leaderboard_key
    }
  }

  template {
    min_replicas = 1
    max_replicas = 3

    container {
      name   = "leaderboard"
      image  = local.image
      cpu    = 0.25
      memory = "0.5Gi"

      env {
        name  = "LEADERBOARD_STORAGE_URL"
        value = azurerm_storage_account.leaderboard.primary_blob_endpoint
      }
      env {
        name  = "AZURE_CLIENT_ID"
        value = azurerm_user_assigned_identity.leaderboard.client_id
      }
      env {
        name        = "LEADERBOARD_ADMIN_KEY"
        secret_name = "admin-key"
      }
      dynamic "env" {
        for_each = var.initial_leaderboard_key == "" ? [] : ["initial-key"]
        content {
          name        = "LEADERBOARD_SUBMIT_KEY"
          secret_name = env.value
        }
      }
      liveness_probe {
        transport = "HTTP"
        port      = 8080
        path      = "/healthz"
      }
      readiness_probe {
        transport = "HTTP"
        port      = 8080
        path      = "/healthz"
      }
    }
  }

  ingress {
    external_enabled           = true
    allow_insecure_connections = false
    target_port                = 8080
    transport                  = "http"

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  lifecycle {
    precondition {
      condition     = var.initial_leaderboard_key != var.admin_key
      error_message = "The event key must be different from the coach admin key."
    }
    precondition {
      condition     = var.container_registry_id == null || var.container_image != null
      error_message = "container_registry_id is only used together with container_image."
    }
  }

  depends_on = [azurerm_role_assignment.blob, azurerm_role_assignment.image, terraform_data.image_build]
}
