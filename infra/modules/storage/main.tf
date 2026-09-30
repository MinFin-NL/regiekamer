terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

resource "azurerm_storage_account" "this" {
  name                          = var.storage_account_name
  resource_group_name           = var.resource_group_name
  location                      = var.location
  account_tier                  = "Standard"
  account_replication_type      = var.replication_type
  account_kind                  = "StorageV2"
  min_tls_version               = "TLS1_2"
  https_traffic_only_enabled    = true
  public_network_access_enabled = true
  # Container Apps mounts the Files share with the account key; disabling key
  # auth breaks the mount.
  shared_access_key_enabled = true
  tags                      = var.tags
}

# SQLite database (regiekamer.db). Mounted at /data in the backend.
resource "azurerm_storage_share" "data" {
  name               = var.file_share_name
  storage_account_id = azurerm_storage_account.this.id
  quota              = var.file_share_quota_gb
}

# Registering the share on the environment is what makes it mountable as an
# AzureFile volume by any app in that environment.
resource "azurerm_container_app_environment_storage" "data" {
  name                         = var.storage_mount_name
  container_app_environment_id = var.container_app_environment_id
  account_name                 = azurerm_storage_account.this.name
  share_name                   = azurerm_storage_share.data.name
  access_key                   = azurerm_storage_account.this.primary_access_key
  access_mode                  = "ReadWrite"
}
