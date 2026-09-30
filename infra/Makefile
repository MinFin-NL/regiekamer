ENV ?= inno-d

TFVARS  := envs/$(ENV).tfvars
BACKEND := envs/$(ENV).backend.hcl

.PHONY: init fmt validate plan apply destroy output

init: ## Initialise with the environment's remote state backend
	tofu init -reconfigure -backend-config=$(BACKEND)

fmt:
	tofu fmt -recursive

validate:
	tofu validate

plan: ## Write a reviewable plan to $(ENV).tfplan
	tofu plan -var-file=$(TFVARS) -out=$(ENV).tfplan

apply: ## Apply the plan produced by `make plan`
	tofu apply $(ENV).tfplan

destroy:
	tofu destroy -var-file=$(TFVARS)

output:
	tofu output
