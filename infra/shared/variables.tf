variable "subscription_id" {
  description = "Coach subscription ID (null = ARM_SUBSCRIPTION_ID). Must be the subscription of the coach stack."
  type        = string
  default     = null
}

variable "teams" {
  description = "Team names (written to teams.auto.tfvars.json by deploy_teams.py from teams.txt)."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for t in var.teams : can(regex("^[a-z0-9][a-z0-9-]{1,19}$", t))])
    error_message = "Team names: 2-20 characters, lowercase letters, digits and hyphens, starting with a letter or digit."
  }

  validation {
    condition     = length(var.teams) == length(distinct(var.teams))
    error_message = "Team names must be unique."
  }
}

variable "coach_state_path" {
  description = "Path to the coach stack's local Terraform state (infra/terraform.tfstate)."
  type        = string
  default     = "../terraform.tfstate"
}

variable "coach_models_json" {
  description = "Coach models.json. Extra keys not managed by the coach state (e.g. `custom` from Own the Weights) are copied to every team."
  type        = string
  default     = "../../shared/config/models.json"
}

variable "default_policy_file" {
  description = "Policy template for every team without an override. Relative paths are resolved from infra/."
  type        = string
  default     = "policies/ai-gateway-starter.xml"
}

variable "team_policy_files" {
  description = "Per-team policy template overrides (team -> path). Managed by set_policy.py."
  type        = map(string)
  default     = {}
}

variable "location" {
  description = "Region of the team resource groups and APIMs (null = coach region)."
  type        = string
  default     = null
}

variable "apim_sku" {
  description = "APIM SKU for team gateways (null = coach apim_sku). Must support the llm-* policies (v2 tiers)."
  type        = string
  default     = null
}

variable "tokens_per_minute_per_consumer" {
  description = "Value passed to the policies as tokens_per_minute (null = coach setting)."
  type        = number
  default     = null
}

variable "output_dir" {
  description = "Folder for the generated per-team files (<output_dir>/<team>/.env and models.json). null = infra/shared/out."
  type        = string
  default     = null
}

variable "tags" {
  description = "Extra tags for team resources (merged with the coach tags)."
  type        = map(string)
  default     = {}
}
