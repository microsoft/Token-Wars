mock_provider "azurerm" {}
mock_provider "random" {}

variables {
  container_image = "example.azurecr.io/tokenwars-leaderboard:test"
  admin_key       = "coach-only-test-secret"
}

run "without_initial_board" {
  command = plan

  assert {
    condition     = length(azurerm_container_app.leaderboard.secret) == 1
    error_message = "A fresh deployment must not silently create an event key."
  }
}

run "with_initial_board" {
  command = plan

  variables {
    initial_leaderboard_key = "first-event-key"
    container_registry_id   = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.ContainerRegistry/registries/test"
  }

  assert {
    condition     = length(azurerm_container_app.leaderboard.secret) == 2 && length(azurerm_container_app.leaderboard.registry) == 1
    error_message = "The initial key and managed-identity registry configuration must be included when provided."
  }
}

run "reject_admin_as_event_key" {
  command = plan

  variables {
    initial_leaderboard_key = "coach-only-test-secret"
  }

  expect_failures = [azurerm_container_app.leaderboard]
}

run "default_builds_image_in_own_acr" {
  command = plan

  variables {
    container_image = null
  }

  assert {
    condition     = length(azurerm_container_registry.leaderboard) == 1 && length(terraform_data.image_build) == 1 && length(azurerm_role_assignment.image) == 1
    error_message = "Without container_image, Terraform must create an ACR, build the image and grant AcrPull."
  }

  assert {
    condition     = azurerm_container_registry.leaderboard[0].admin_enabled == false && length(azurerm_container_app.leaderboard.registry) == 1
    error_message = "The built image must be pulled with managed identity, not registry admin credentials."
  }
}

run "prebuilt_image_skips_acr" {
  command = plan

  assert {
    condition     = length(azurerm_container_registry.leaderboard) == 0 && length(terraform_data.image_build) == 0
    error_message = "A prebuilt container_image must not create a registry or run a build."
  }
}
