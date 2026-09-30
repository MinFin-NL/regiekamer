variable "name" {
  type        = string
  description = "Name of the container app (e.g. ca-regiekamer-backend-inno-d)."
}

variable "resource_group_name" {
  type = string
}

variable "container_app_environment_id" {
  type = string
}

variable "image" {
  type        = string
  description = "Fully qualified image reference, including registry and tag."
}

variable "cpu" {
  type    = number
  default = 0.5
}

variable "memory" {
  type    = string
  default = "1Gi"
}

variable "min_replicas" {
  type    = number
  default = 1
}

variable "max_replicas" {
  type    = number
  default = 1
}

variable "command" {
  type    = list(string)
  default = []
}

variable "args" {
  type    = list(string)
  default = []
}

variable "env" {
  type        = map(string)
  description = "Plain environment variables."
  default     = {}
}

variable "secret_env" {
  type        = map(string)
  description = "Environment variables sourced from secrets: env var name => secret name."
  default     = {}
}

variable "secrets" {
  type        = map(string)
  description = "Secret name => value. Names must be lowercase alphanumeric with dashes."
  default     = {}
  sensitive   = true
}

variable "registry" {
  type = object({
    server   = string
    username = string
    password = string
  })
  description = "Container registry to pull from. Null for public images."
  default     = null
  sensitive   = true
}

variable "ingress" {
  type = object({
    external    = bool
    target_port = number
    ip_restrictions = optional(list(object({
      name             = string
      ip_address_range = string
    })), [])
  })
  description = "Ingress configuration. Null disables ingress entirely."
  default     = null
}

variable "volumes" {
  type = list(object({
    name         = string
    storage_name = string
    mount_path   = string
  }))
  description = "Azure Files volumes registered on the environment, mounted into the container."
  default     = []
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "system_assigned_identity" {
  type        = bool
  description = "Give the app a system-assigned managed identity."
  default     = false
}
