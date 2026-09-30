terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

# In the MinFin landing zone the resource group is usually handed to the team by
# the CCoE. Set create_resource_group = false there and tofu will adopt it.
resource "azurerm_resource_group" "this" {
  count = var.create_resource_group ? 1 : 0

  name     = var.resource_group_name
  location = var.location
  tags     = var.tags
}

data "azurerm_resource_group" "existing" {
  count = var.create_resource_group ? 0 : 1

  name = var.resource_group_name
}

locals {
  resource_group = var.create_resource_group ? azurerm_resource_group.this[0] : data.azurerm_resource_group.existing[0]
}

resource "azurerm_container_registry" "this" {
  name                = var.container_registry_name
  resource_group_name = local.resource_group.name
  location            = local.resource_group.location
  sku                 = var.container_registry_sku
  # Admin credentials are how the container apps authenticate against ACR
  # today, mirroring the Azure DevOps pipeline. Switching to a managed identity
  # pull is the intended next step — see README, "Known gaps".
  admin_enabled = true
  tags          = var.tags
}

resource "azurerm_log_analytics_workspace" "this" {
  name                = var.log_analytics_workspace_name
  resource_group_name = local.resource_group.name
  location            = local.resource_group.location
  sku                 = "PerGB2018"
  retention_in_days   = var.log_retention_in_days
  tags                = var.tags
}

resource "azurerm_container_app_environment" "this" {
  name                       = var.container_app_environment_name
  resource_group_name        = local.resource_group.name
  location                   = local.resource_group.location
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id
  tags                       = var.tags
}
