variable "storage_account_name" {
  type        = string
  description = "Globally unique, 3-24 lowercase alphanumeric characters."
}

variable "resource_group_name" {
  type = string
}

variable "location" {
  type = string
}

variable "container_app_environment_id" {
  type = string
}

variable "replication_type" {
  type    = string
  default = "LRS"
}

variable "file_share_name" {
  type    = string
  default = "regiekamer-data"
}

variable "file_share_quota_gb" {
  type    = number
  default = 4
}

variable "storage_mount_name" {
  type        = string
  description = "Name the share is registered under on the Container Apps environment."
  default     = "regiekamer-data"
}

variable "tags" {
  type    = map(string)
  default = {}
}
