# Remote state for the inno-d environment. Create these once, out of band
# (or reuse invulhulp's state account with a different key):
#
#   az group create -n rg-regiekamer-tfstate -l westeurope
#   az storage account create -n stregiekamertfstate -g rg-regiekamer-tfstate \
#     -l westeurope --sku Standard_LRS --kind StorageV2 --min-tls-version TLS1_2
#   az storage container create -n tfstate --account-name stregiekamertfstate
#
# Then: tofu init -backend-config=envs/inno-d.backend.hcl

resource_group_name  = "rg-regiekamer-tfstate"
storage_account_name = "stregiekamertfstate"
container_name       = "tfstate"
key                  = "inno-d.tfstate"
use_azuread_auth     = true
