locals {
  backend_app_name  = "ca-regiekamer-backend-${var.environment}"
  frontend_app_name = "ca-regiekamer-frontend-${var.environment}"

  tags = merge({
    application = "regiekamer"
    environment = var.environment
    managed_by  = "opentofu"
  }, var.tags)

  # Derived from the environment's domain rather than the backend resource, so
  # the frontend doesn't wait on (or cycle with) the backend.
  default_domain = module.platform.container_app_environment_default_domain
  backend_url    = "https://${local.backend_app_name}.internal.${local.default_domain}"

  registry = {
    server   = module.platform.registry_login_server
    username = module.platform.registry_username
    password = module.platform.registry_password
  }
}

module "platform" {
  source = "./modules/platform"

  resource_group_name            = var.resource_group_name
  create_resource_group          = var.create_resource_group
  location                       = var.location
  container_registry_name        = var.container_registry_name
  log_analytics_workspace_name   = "log-regiekamer-${var.environment}"
  container_app_environment_name = "cae-regiekamer-${var.environment}"
  tags                           = local.tags
}

module "storage" {
  source = "./modules/storage"

  storage_account_name         = var.storage_account_name
  resource_group_name          = module.platform.resource_group_name
  location                     = module.platform.location
  container_app_environment_id = module.platform.container_app_environment_id
  tags                         = local.tags
}

module "backend" {
  source = "./modules/container_app"

  name                         = local.backend_app_name
  resource_group_name          = module.platform.resource_group_name
  container_app_environment_id = module.platform.container_app_environment_id
  image                        = "${module.platform.registry_login_server}/${var.backend_image_name}:${var.image_tag}"
  registry                     = local.registry
  cpu                          = 0.5
  memory                       = "1Gi"
  # SQLite on an SMB share: exactly one writer.
  min_replicas             = 1
  max_replicas             = 1
  system_assigned_identity = true
  tags                     = local.tags

  ingress = {
    external    = false
    target_port = 8000
  }

  volumes = [{
    name         = "data"
    storage_name = module.storage.storage_mount_name
    mount_path   = "/data"
  }]

  env = {
    DB_PATH                  = "/data/regiekamer.db"
    CORS_ORIGINS             = "*"
    AGENT_RUNTIME_DEFAULT    = var.agent_runtime_default
    FOUNDRY_PROJECT_ENDPOINT = var.foundry_project_endpoint
    FOUNDRY_MODEL_DEPLOYMENT = var.foundry_model_deployment
    AZURE_OPENAI_DEPLOYMENT  = var.azure_openai_deployment
    AZURE_OPENAI_API_VERSION = var.azure_openai_api_version
  }

  secret_env = {
    AZURE_OPENAI_ENDPOINT = "openai-endpoint"
    AZURE_OPENAI_API_KEY  = "openai-api-key"
  }

  secrets = {
    "openai-endpoint" = var.azure_openai_endpoint
    "openai-api-key"  = var.azure_openai_api_key
  }
}

# Foundry Agent Service accepts Entra ID only. "Azure AI User" lets the backend
# create/version/delete agents and run responses in the project. The identity
# running `tofu apply` needs rights to create role assignments on this scope.
resource "azurerm_role_assignment" "backend_foundry" {
  count = var.foundry_project_resource_id == "" ? 0 : 1

  scope                = var.foundry_project_resource_id
  role_definition_name = "Azure AI User"
  principal_id         = module.backend.principal_id
  principal_type       = "ServicePrincipal"
}

module "frontend" {
  source = "./modules/container_app"

  name                         = local.frontend_app_name
  resource_group_name          = module.platform.resource_group_name
  container_app_environment_id = module.platform.container_app_environment_id
  image                        = "${module.platform.registry_login_server}/${var.frontend_image_name}:${var.image_tag}"
  registry                     = local.registry
  cpu                          = 0.25
  memory                       = "0.5Gi"
  min_replicas                 = 1
  max_replicas                 = 2
  tags                         = local.tags

  ingress = {
    external        = true
    target_port     = 80
    ip_restrictions = var.frontend_ip_restrictions
  }

  env = {
    BACKEND_URL = local.backend_url
  }
}
