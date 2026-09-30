output "id" {
  value = azurerm_container_app.this.id
}

output "name" {
  value = azurerm_container_app.this.name
}

output "fqdn" {
  description = "Ingress FQDN, or null when the app has no ingress."
  value       = var.ingress == null ? null : azurerm_container_app.this.ingress[0].fqdn
}

output "url" {
  value = var.ingress == null ? null : "https://${azurerm_container_app.this.ingress[0].fqdn}"
}

output "principal_id" {
  description = "Object id of the system-assigned identity, or null."
  value       = var.system_assigned_identity ? azurerm_container_app.this.identity[0].principal_id : null
}
