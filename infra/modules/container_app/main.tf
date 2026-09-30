terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

locals {
  # The registry password is passed as a normal secret so the container app can
  # reference it by name; azurerm requires that indirection.
  registry_secrets = var.registry == null ? {} : {
    "registry-password" = var.registry.password
  }

  all_secrets = merge(var.secrets, local.registry_secrets)
}

resource "azurerm_container_app" "this" {
  name                         = var.name
  resource_group_name          = var.resource_group_name
  container_app_environment_id = var.container_app_environment_id
  revision_mode                = "Single"
  tags                         = var.tags

  # Lets the app authenticate to Entra-only services (Foundry Agent Service)
  # through DefaultAzureCredential, without a secret.
  dynamic "identity" {
    for_each = var.system_assigned_identity ? [1] : []
    content {
      type = "SystemAssigned"
    }
  }

  dynamic "secret" {
    for_each = local.all_secrets
    content {
      name  = secret.key
      value = secret.value
    }
  }

  dynamic "registry" {
    for_each = var.registry == null ? [] : [var.registry]
    content {
      server               = registry.value.server
      username             = registry.value.username
      password_secret_name = "registry-password"
    }
  }

  dynamic "ingress" {
    for_each = var.ingress == null ? [] : [var.ingress]
    content {
      external_enabled = ingress.value.external
      target_port      = ingress.value.target_port
      transport        = "auto"

      traffic_weight {
        latest_revision = true
        percentage      = 100
      }

      dynamic "ip_security_restriction" {
        for_each = ingress.value.ip_restrictions
        content {
          name             = ip_security_restriction.value.name
          ip_address_range = ip_security_restriction.value.ip_address_range
          action           = "Allow"
          description      = ip_security_restriction.value.name
        }
      }
    }
  }

  template {
    min_replicas = var.min_replicas
    max_replicas = var.max_replicas

    dynamic "volume" {
      for_each = var.volumes
      content {
        name         = volume.value.name
        storage_name = volume.value.storage_name
        storage_type = "AzureFile"
      }
    }

    container {
      name   = var.name
      image  = var.image
      cpu    = var.cpu
      memory = var.memory

      args    = var.args
      command = var.command

      dynamic "env" {
        for_each = var.env
        content {
          name  = env.key
          value = env.value
        }
      }

      # Values that must not surface in plan output or the ARM template: the
      # container app resolves them from its own secret store at start-up.
      dynamic "env" {
        for_each = var.secret_env
        content {
          name        = env.key
          secret_name = env.value
        }
      }

      dynamic "volume_mounts" {
        for_each = var.volumes
        content {
          name = volume_mounts.value.name
          path = volume_mounts.value.mount_path
        }
      }
    }
  }
}
