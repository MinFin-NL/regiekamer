output "account_name" {
  value = azurerm_storage_account.this.name
}

output "account_key" {
  value     = azurerm_storage_account.this.primary_access_key
  sensitive = true
}

output "storage_mount_name" {
  value = azurerm_container_app_environment_storage.data.name
}
