terraform {
  required_providers {
    azurerm = {
      source = "hashicorp/azurerm"
    }
    azapi = {
      source = "Azure/azapi"
    }
    local = {
      source = "hashicorp/local"
    }
  }
}

variable "team" {
  type = string
}

variable "location" {
  type = string
}

variable "suffix" {
  description = "Random suffix of the coach stack (keeps global names unique)."
  type        = string
}

variable "tags" {
  type = map(string)
}

variable "apim_sku" {
  type = string
}

variable "apim_publisher_email" {
  type = string
}

variable "apim_consumers" {
  type = list(string)
}

variable "tokens_per_minute" {
  type = number
}

variable "pool_models" {
  type = list(string)
}

variable "judge_deployment" {
  type = string
}

variable "primary" {
  description = "Coach primary Foundry resource: { id, subdomain }."
  type        = any
}

variable "secondary" {
  description = "Coach secondary Foundry resource { id, subdomain, location } or null."
  type        = any
}

variable "fireworks" {
  description = "Coach Fireworks resource { id, backend_url, base_url, auth, api_key, models, failover_mapping } or null."
  type        = any
}

variable "selfhosted" {
  description = "Coach Ollama app { base_url, model } or null."
  type        = any
}

variable "log_analytics_workspace_id" {
  type = string
}

variable "policy_file" {
  description = "Absolute path of the policy template for the team's openai API."
  type        = string
}

variable "judge_policy_file" {
  type = string
}

variable "models" {
  description = "models.json entries for every non-judge key (already pointed at the gateway)."
  type        = any
}

variable "judge_model" {
  description = "Coach judge entry; base_url and key are replaced with the team's judge API."
  type        = any
}

variable "leaderboard_url" {
  type = string
}

variable "leaderboard_key" {
  type      = string
  sensitive = true
}

variable "output_dir" {
  type = string
}
