output "leaderboard_url" {
  description = "Leaderboard website and participant submission URL."
  value       = "https://${azurerm_container_app.leaderboard.ingress[0].fqdn}"
}

output "storage_account_name" {
  description = "Persistent storage account; do not delete after an event."
  value       = azurerm_storage_account.leaderboard.name
}

output "storage_url" {
  description = "Blob service URL for local verification or reusing this storage."
  value       = azurerm_storage_account.leaderboard.primary_blob_endpoint
}

output "resource_group" {
  description = "Resource group holding the app and its storage. Deleting it deletes all leaderboard data."
  value       = azurerm_resource_group.leaderboard.name
}
