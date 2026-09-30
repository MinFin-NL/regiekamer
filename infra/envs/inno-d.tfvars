# Non-secret settings for the inno-d environment. Secrets (the optional Azure
# OpenAI key) come in as TF_VAR_* from the regiekamer-secrets variable group.

subscription_id = "00000000-0000-0000-0000-000000000000" # TODO: same subscription as invulhulp

environment           = "inno-d"
location              = "westeurope"
resource_group_name   = "rg-regiekamer-inno-d"
create_resource_group = false

container_registry_name = "crregiekamerinnod"
storage_account_name    = "stregiekamerinnod"

image_tag = "latest"

# Same allow-list as invulhulp's frontend.
frontend_ip_restrictions = [
  # { name = "DWR Next werkplekken", ip_address_range = "x.x.x.x/32" },
  # { name = "ITS", ip_address_range = "y.y.y.y/32" },
]

agent_runtime_default = "foundry"

# TODO: fill in once the Foundry project exists.
foundry_project_endpoint    = "" # https://<resource>.services.ai.azure.com/api/projects/<project>
foundry_project_resource_id = "" # /subscriptions/.../providers/Microsoft.CognitiveServices/accounts/<resource>/projects/<project>
foundry_model_deployment    = "gpt-5-mini"

azure_openai_deployment  = "gpt-5.3-chat"
azure_openai_api_version = "2025-04-01-preview"

tags = {
  owner    = "regiekamer"
  costtype = "innovatie"
}
