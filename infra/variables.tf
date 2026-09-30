# ── Environment ──────────────────────────────────────────────────────────────

variable "subscription_id" {
  type        = string
  description = "Subscription the environment lives in."
}

variable "environment" {
  type        = string
  description = "Short environment name, used in resource names (e.g. inno-d)."
}

variable "location" {
  type    = string
  default = "westeurope"
}

variable "resource_group_name" {
  type = string
}

variable "create_resource_group" {
  type        = bool
  description = "False when the landing zone already provisioned the resource group."
  default     = false
}

variable "container_registry_name" {
  type = string
}

variable "storage_account_name" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

# ── Application ──────────────────────────────────────────────────────────────

variable "backend_image_name" {
  type    = string
  default = "regiekamer-backend"
}

variable "frontend_image_name" {
  type    = string
  default = "regiekamer-frontend"
}

variable "image_tag" {
  type        = string
  description = <<-EOT
    Tag for both images. Keep "latest" while the app pipeline (azure-pipelines.yml)
    owns rollouts; pin a git SHA here if this configuration should own them instead.
  EOT
  default     = "latest"
}

variable "frontend_ip_restrictions" {
  type = list(object({
    name             = string
    ip_address_range = string
  }))
  description = "Allow-list on the public frontend. The demo has no login, so this is the gate."
  default     = []
}

variable "agent_runtime_default" {
  type        = string
  description = "Runtime for new hires: foundry | chat | mock."
  default     = "foundry"
}

# ── Azure AI Foundry ─────────────────────────────────────────────────────────

variable "foundry_project_endpoint" {
  type        = string
  description = "https://<resource>.services.ai.azure.com/api/projects/<project>"
  default     = ""
}

variable "foundry_project_resource_id" {
  type        = string
  description = <<-EOT
    Resource id of the Foundry project (or its parent AI Services account). The
    backend's managed identity gets "Azure AI User" on it. Empty = skip the role
    assignment and do it by hand.
  EOT
  default     = ""
}

variable "foundry_model_deployment" {
  type    = string
  default = "gpt-5-mini"
}

# ── Azure OpenAI (chat runtime, optional) ────────────────────────────────────

variable "azure_openai_endpoint" {
  type      = string
  default   = ""
  sensitive = true
}

variable "azure_openai_api_key" {
  type      = string
  default   = ""
  sensitive = true
}

variable "azure_openai_deployment" {
  type    = string
  default = "gpt-5.3-chat"
}

variable "azure_openai_api_version" {
  type    = string
  default = "2025-04-01-preview"
}
