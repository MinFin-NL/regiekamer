output "resource_group_name" {
  value = local.resource_group.name
}

output "location" {
  value = local.resource_group.location
}

output "container_app_environment_id" {
  value = azurerm_container_app_environment.this.id
}

output "container_app_environment_name" {
  value = azurerm_container_app_environment.this.name
}

output "registry_login_server" {
  value = azurerm_container_registry.this.login_server
}

output "registry_username" {
  value = azurerm_container_registry.this.admin_username
}

output "registry_password" {
  value     = azurerm_container_registry.this.admin_password
  sensitive = true
}

output "container_app_environment_default_domain" {
  description = "Domain container apps get their FQDN under. Lets dependent apps compute each other's URLs without a dependency cycle."
  value       = azurerm_container_app_environment.this.default_domain
}
