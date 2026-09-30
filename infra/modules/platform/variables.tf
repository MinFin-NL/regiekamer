variable "resource_group_name" {
  type = string
}

variable "create_resource_group" {
  type        = bool
  description = "False when the landing zone already provisioned the resource group."
  default     = false
}

variable "location" {
  type        = string
  description = "Azure region. Only used when create_resource_group is true."
  default     = "westeurope"
}

variable "container_registry_name" {
  type        = string
  description = "Globally unique, 5-50 lowercase alphanumeric characters."
}

variable "container_registry_sku" {
  type    = string
  default = "Basic"
}

variable "log_analytics_workspace_name" {
  type = string
}

variable "log_retention_in_days" {
  type    = number
  default = 30
}

variable "container_app_environment_name" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}
