output "frontend_url" {
  description = "Public URL of the Regiekamer."
  value       = module.frontend.url
}

output "backend_internal_url" {
  description = "Environment-internal URL the frontend proxies /api to."
  value       = module.backend.url
}

output "backend_principal_id" {
  description = "Managed identity of the backend; needs 'Azure AI User' on the Foundry project."
  value       = module.backend.principal_id
}

output "registry_login_server" {
  description = "Push target for the application pipeline's image builds."
  value       = module.platform.registry_login_server
}

output "resource_group_name" {
  value = module.platform.resource_group_name
}

output "container_app_environment_name" {
  value = module.platform.container_app_environment_name
}

output "storage_account_name" {
  value = module.storage.account_name
}
