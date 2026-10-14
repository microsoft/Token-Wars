# -----------------------------------------------------------------------------
# General
# -----------------------------------------------------------------------------
variable "subscription_id" {
  description = "Azure subscription ID. Leave null to use the ARM_SUBSCRIPTION_ID environment variable."
  type        = string
  default     = null
}

variable "team_name" {
  description = "Your team name (lowercase letters, digits and hyphens, 2-20 chars). Used in resource names, .env and on the leaderboard."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,19}$", var.team_name))
    error_message = "team_name must be 2-20 chars of lowercase letters, digits and hyphens, starting with a letter or digit."
  }
}

variable "location" {
  description = "Azure region for all primary resources."
  type        = string
  default     = "swedencentral"
}

variable "resource_group_name" {
  description = "Resource group name. Defaults to rg-tokenwars-<team_name>."
  type        = string
  default     = null
}

variable "tags" {
  description = "Extra tags for all resources."
  type        = map(string)
  default     = {}
}

# -----------------------------------------------------------------------------
# Microsoft Foundry (new resource model: Foundry resource = AIServices account with project management + project)
# -----------------------------------------------------------------------------
variable "foundry_project_name" {
  description = "Name of the Foundry project created under the Foundry resource (also used for the Fireworks account's project)."
  type        = string
  default     = "proj-tokenwars"
}

variable "ai_endpoint_style" {
  description = "Host used in models.json base_url: \"foundry\" = https://<subdomain>.services.ai.azure.com/openai/v1/ (default, new Foundry domain), \"openai\" = https://<subdomain>.openai.azure.com/openai/v1/ (fallback). Both serve the same OpenAI-compatible v1 API."
  type        = string
  default     = "foundry"

  validation {
    condition     = contains(["foundry", "openai"], var.ai_endpoint_style)
    error_message = "ai_endpoint_style must be \"foundry\" or \"openai\"."
  }
}

# -----------------------------------------------------------------------------
# Models (deployments on the Foundry resource)
#
# Every model variable has the same shape:
#   name                 = model name in the catalog
#   version              = model version (null = the current default version in the region)
#   format               = model publisher format ("OpenAI", "Meta", ...)
#   sku                  = deployment type ("GlobalStandard", "DataZoneStandard", "Standard", ...)
#   capacity             = throughput in units of 1,000 tokens per minute (TPM) for Standard/GlobalStandard
#   pricing_key          = key in shared/config/pricing.json (change it if you change the model!)
#   max_tokens_param     = "max_completion_tokens" (GPT-5.x reasoning-capable models) or "max_tokens" (GPT-4.1, Llama)
#   supports_temperature = false for GPT-5.x (the apps then omit "temperature"), true for GPT-4.1 / Llama
#   extra_body           = string map merged into every chat request body, e.g. { reasoning_effort = "none" }
#                          (GPT-5.x: "none" | "low" | "medium" | "high"; reasoning tokens are billed as output tokens)
#   extra_headers        = string map added as HTTP headers to every request for that model (default {})
#
# Answer defaults use GPT-5.6: Sol (premium), Terra (balanced), Luna (economy).
# The independent judge uses GPT-5.5, pinned separately from the answer tiers.
# The GPT-4.1 family is deprecated for new customers in 2026;
# its values are kept as a commented alternative in terraform.tfvars.example.
# The baseline sends ~15k prompt tokens per call with 8 parallel workers, so the premium deployment needs a
# generous TPM capacity. Quota is per subscription + region + model + deployment type: check it in the Foundry
# portal > Quota (or az cognitiveservices usage list --location <region> -o table) before the event.
# -----------------------------------------------------------------------------

# Verify availability first: az cognitiveservices model list --location <region> --query "[?model.name=='gpt-5.6-sol']" -o table
variable "premium_model" {
  description = "Premium tier: highest-capability chat model. The tier name is provider-neutral; configure the catalog model and request settings below."
  type = object({
    name                 = optional(string, "gpt-5.6-sol")
    version              = optional(string, "2026-07-09")
    format               = optional(string, "OpenAI")
    sku                  = optional(string, "GlobalStandard")
    capacity             = optional(number, 800)
    pricing_key          = optional(string, "gpt-5.6-sol")
    max_tokens_param     = optional(string, "max_completion_tokens")
    supports_temperature = optional(bool, false)
    extra_body           = optional(map(string), { reasoning_effort = "none" })
    extra_headers        = optional(map(string), {})
  })
  default = {}
}

# Verify availability first: az cognitiveservices model list --location <region> --query "[?model.name=='gpt-5.6-terra']" -o table
variable "balanced_model" {
  description = "Balanced tier: chat model balancing answer quality and cost, independent of provider."
  type = object({
    name                 = optional(string, "gpt-5.6-terra")
    version              = optional(string, "2026-07-09")
    format               = optional(string, "OpenAI")
    sku                  = optional(string, "GlobalStandard")
    capacity             = optional(number, 800)
    pricing_key          = optional(string, "gpt-5.6-terra")
    max_tokens_param     = optional(string, "max_completion_tokens")
    supports_temperature = optional(bool, false)
    extra_body           = optional(map(string), { reasoning_effort = "none" })
    extra_headers        = optional(map(string), {})
  })
  default = {}
}

# Verify availability first: az cognitiveservices model list --location <region> --query "[?model.name=='gpt-5.6-luna']" -o table
variable "economy_model" {
  description = "Economy tier: lowest-cost chat model (also used by the classifier router), independent of provider."
  type = object({
    name                 = optional(string, "gpt-5.6-luna")
    version              = optional(string, "2026-07-09")
    format               = optional(string, "OpenAI")
    sku                  = optional(string, "GlobalStandard")
    capacity             = optional(number, 800)
    pricing_key          = optional(string, "gpt-5.6-luna")
    max_tokens_param     = optional(string, "max_completion_tokens")
    supports_temperature = optional(bool, false)
    extra_body           = optional(map(string), { reasoning_effort = "none" })
    extra_headers        = optional(map(string), {})
  })
  default = {}
}

# Verify availability first: az cognitiveservices model list --location <region> --query "[?model.name=='gpt-5.5']" -o table
variable "judge_model" {
  description = "Independent GPT-5.5 evaluator, deployed as 'judge' with its own throughput allocation. Bypasses APIM; evaluation cost is excluded from the team score."
  type = object({
    name                 = optional(string, "gpt-5.5")
    version              = optional(string, "2026-04-24")
    format               = optional(string, "OpenAI")
    sku                  = optional(string, "GlobalStandard")
    capacity             = optional(number, 800)
    pricing_key          = optional(string, "gpt-5.5")
    max_tokens_param     = optional(string, "max_completion_tokens")
    supports_temperature = optional(bool, false)
    extra_body           = optional(map(string), { reasoning_effort = "none" })
    extra_headers        = optional(map(string), {})
  })
  default = {}
}

# Verify availability first: az cognitiveservices model list --location <region> --query "[?model.name=='text-embedding-3-small']" -o table
# (if GlobalStandard is not offered for embeddings in your region, use sku = "Standard")
# max_tokens_param / supports_temperature / extra_body are not used for embeddings (written for schema completeness).
variable "embedding_model" {
  description = "Embedding model for the semantic cache and embedding retrieval."
  type = object({
    name                 = optional(string, "text-embedding-3-small")
    version              = optional(string, "1")
    format               = optional(string, "OpenAI")
    sku                  = optional(string, "GlobalStandard")
    capacity             = optional(number, 150)
    pricing_key          = optional(string, "text-embedding-3-small")
    max_tokens_param     = optional(string, "max_tokens")
    supports_temperature = optional(bool, true)
    extra_body           = optional(map(string), {})
    extra_headers        = optional(map(string), {})
  })
  default = {}
}

# Verify availability first: az cognitiveservices model list --location <region> --query "[?model.name=='Llama-3.3-70B-Instruct']" -o table
# Llama-3.3-70B-Instruct is a Foundry model "sold directly by Azure" (format "Meta", no Marketplace subscription needed).
# version = null lets Azure pick the current default version (the catalog version number changes over time).
variable "open_model" {
  description = "Open-weight model deployed as a serverless (pay-per-token) Foundry deployment."
  type = object({
    name                 = optional(string, "Llama-3.3-70B-Instruct")
    version              = optional(string)
    format               = optional(string, "Meta")
    sku                  = optional(string, "GlobalStandard")
    capacity             = optional(number, 100)
    pricing_key          = optional(string, "llama-3.3-70b-instruct")
    max_tokens_param     = optional(string, "max_tokens")
    supports_temperature = optional(bool, true)
    extra_body           = optional(map(string), {})
    extra_headers        = optional(map(string), {})
  })
  default = {}
}

variable "deploy_open_model" {
  description = "Deploy the open-weight model (models.json key 'open')."
  type        = bool
  default     = true
}

# -----------------------------------------------------------------------------
# AI gateway (Azure API Management)
# -----------------------------------------------------------------------------
variable "deploy_apim" {
  description = "Required AI gateway for all inference except the judge. Provisioning a v2 tier takes ~5-15 minutes; false is not supported."
  type        = bool
  default     = true

  validation {
    condition     = var.deploy_apim
    error_message = "APIM is required. Keep deploy_apim = true; only the judge calls its model directly."
  }
}

variable "apim_sku" {
  description = "APIM SKU '<name>_<units>'. BasicV2/StandardV2 support llm-token-limit and llm-emit-token-metric (Consumption does not support llm-token-limit)."
  type        = string
  default     = "StandardV2_1"
}

variable "apim_publisher_email" {
  description = "Publisher email required by APIM."
  type        = string
  default     = "tokenwars@example.com"
}

variable "apim_policy_file" {
  description = "API policy template (relative to infra/). policies/ai-gateway-solution.xml = reference solution; policies/ai-gateway-multiprovider.xml = Challenge 3.6 stretch (needs deploy_fireworks = true)."
  type        = string
  default     = "policies/ai-gateway-starter.xml"
}

variable "apim_consumers" {
  description = "One APIM subscription (key) per consumer team. The app uses the 'support-squad' key (or the first one)."
  type        = list(string)
  default     = ["checkout-squad", "support-squad"]

  validation {
    condition     = length(var.apim_consumers) > 0
    error_message = "apim_consumers must contain at least one consumer."
  }
}

variable "tokens_per_minute_per_consumer" {
  description = "Token-per-minute limit per APIM subscription, passed to the policy template (llm-token-limit)."
  type        = number
  default     = 20000
}

variable "failover_model_map" {
  description = "Multi-provider failover (policies/ai-gateway-multiprovider.xml): Azure deployment name sent by the app -> Fireworks deployment name. null maps both balanced and premium to the single default fw deployment. Requests for unmapped models are never failed over."
  type        = map(string)
  default     = null
}

# -----------------------------------------------------------------------------
# Self-hosted small model (Ollama on Azure Container Apps, CPU only)
# -----------------------------------------------------------------------------
variable "deploy_selfhosted_model" {
  description = "Deploy Ollama on Azure Container Apps (CPU) and register it as models.json key 'selfhosted'."
  type        = bool
  default     = false
}

variable "selfhosted_model" {
  description = "Ollama model tag pulled at container start."
  type        = string
  default     = "llama3.2:3b"
}

variable "selfhosted_cpu" {
  description = "vCPU for the Ollama container (Consumption plan max 4)."
  type        = number
  default     = 4
}

variable "selfhosted_memory" {
  description = "Memory for the Ollama container (must match the CPU ratio 1:2, e.g. 4 vCPU = 8Gi)."
  type        = string
  default     = "8Gi"
}

# -----------------------------------------------------------------------------
# Secondary region (failover demo for the APIM backend pool)
# -----------------------------------------------------------------------------
variable "deploy_secondary_region" {
  description = "Deploy a second Foundry resource (premium/balanced/economy with the same deployment names) as failover backend."
  type        = bool
  default     = false
}

variable "secondary_location" {
  description = "Region of the secondary Foundry resource."
  type        = string
  default     = "francecentral"
}

variable "secondary_capacity" {
  description = "TPM capacity (x1000) for each secondary chat deployment."
  type        = number
  default     = 800
}

# -----------------------------------------------------------------------------
# Fireworks on Foundry (Challenge 2.4 "Fireworks Arena", optional, PAY-PER-TOKEN ONLY - never PTU)
#
# Creates a SECOND Foundry resource (+ project) in fireworks_location and one deployment per fireworks_models entry.
# Prerequisites (run infra/scripts/check-fireworks-prereqs.sh first, during setup - NOT during the challenge):
#   * subscription feature Fireworks.EnableDeploy registered (Subscription Owner/Contributor, up to 30 min)
#   * deploying needs Owner/Contributor or "Foundry Owner" (formerly "Azure AI Owner") on the Foundry project
#   * Data Zone Standard (pay-per-token) is US-only: eastus, eastus2, centralus, northcentralus, westus, westus3
#   * a Fireworks deployment can take up to 30 minutes
# Fireworks on Foundry is currently excluded from the EU Data Boundary (documented limitation, not a blocker for
# the workshop's synthetic data). Chat completions only (no embeddings). Per-token models can retire with 15 days notice.
# -----------------------------------------------------------------------------
variable "deploy_fireworks" {
  description = "Deploy the Fireworks catalog model (pay-per-token) on a second Foundry resource. Adds the models.json key fw."
  type        = bool
  default     = false
}

variable "fireworks_location" {
  description = "Region of the Fireworks Foundry resource. The default Global Standard model uses eastus2; Data Zone Standard is limited to eastus, eastus2, centralus, northcentralus, westus and westus3."
  type        = string
  default     = "eastus2"
}

# Model IDs are the Foundry catalog IDs (Foundry portal > Discover > Models, filter "Fireworks").
# Current default (check availability one week before the event - per-token offers can retire with 15 days notice):
#   Global Standard: FW-GLM-5.3-Flash
# Alternatives: FW-DeepSeek-V4.1-Flash or FW-Kimi-K3 (Global Standard);
# FW-GLM-5.3, FW-GLM-5.2-Fast, FW-MiniMax-M3 or FW-Nemotron-Lightning-3.5-30B-A3B (Data Zone Standard).
# If you switch models, add a matching key to shared/config/pricing.json and set pricing_key.
# Small open models (Llama 3.1 8B, Qwen3.5 9B, gpt-oss-20b, Ministral 3B) are PTU-only on Fireworks -> not usable here.
#   model                = catalog model ID (also the default deployment name)
#   deployment_name      = optional deployment name override (this is the "model" value the apps send)
#   version              = null = current default version
#   sku                  = "DataZoneStandard" (US data zone, per-token) or "GlobalStandard" (per-token)
#   capacity             = TPM rate limit in thousands (the Data Zone / Global pools default to 10M TPM per region,
#                          shared by all Fireworks models; Fireworks also applies adaptive rate limits -> 429s)
#   extra_body           = merged into every chat request; prompt_cache_key improves Fireworks prompt-cache hit rate
#   extra_headers        = e.g. { "x-session-affinity" = "bytecart" } (alternative way to pin the prompt cache)
variable "fireworks_models" {
  description = "Fireworks catalog models to deploy (models.json key -> settings). Use fw for the default model; additional keys must start with fw_."
  type = map(object({
    model                = string
    deployment_name      = optional(string)
    version              = optional(string)
    sku                  = optional(string, "DataZoneStandard")
    capacity             = optional(number, 200)
    pricing_key          = string
    max_tokens_param     = optional(string, "max_tokens")
    supports_temperature = optional(bool, true)
    extra_body           = optional(map(string), { prompt_cache_key = "bytecart-support" })
    extra_headers        = optional(map(string), {})
  }))
  default = {
    fw = {
      model       = "FW-GLM-5.3-Flash"
      sku         = "GlobalStandard"
      pricing_key = "fw-glm-5.3-flash"
    }
  }

  validation {
    condition     = length(var.fireworks_models) > 0 && alltrue([for k in keys(var.fireworks_models) : k == "fw" || can(regex("^fw_[a-z0-9_]+$", k))])
    error_message = "fireworks_models needs at least one entry; use key fw or keys matching ^fw_[a-z0-9_]+$."
  }

  validation {
    condition     = alltrue([for m in values(var.fireworks_models) : contains(["DataZoneStandard", "GlobalStandard"], m.sku)])
    error_message = "Only pay-per-token SKUs are allowed in this kit: DataZoneStandard or GlobalStandard (no PTU)."
  }
}

# VALIDATE: deployment model "format" for Fireworks catalog models. "Fireworks" is used by community Terraform samples;
# Microsoft Learn documents "FireworksCustom" for imported custom weights. Confirm with:
#   az cognitiveservices account list-models -n <fireworks-account> -g <rg> \
#     --query "[?starts_with(name,'FW-')].{name:name, format:format, version:version, sku:skus[0].name}" -o table
variable "fireworks_model_format" {
  description = "Model format (publisher) of Fireworks catalog models in Microsoft.CognitiveServices/accounts/deployments."
  type        = string
  default     = "Fireworks"
}

variable "fireworks_grant_deployer_role" {
  description = "Assign fireworks_deployer_role to the identity running Terraform on the Fireworks Foundry project before deploying (enable if deployments fail with Forbidden)."
  type        = bool
  default     = false
}

variable "fireworks_deployer_role" {
  description = "Role required to deploy Fireworks models. Renamed in 2026: use \"Azure AI Owner\" if your tenant does not know \"Foundry Owner\" yet (same role ID)."
  type        = string
  default     = "Foundry Owner"
}

variable "fireworks_backend_auth" {
  description = "How all gateway policies authenticate to Fireworks: \"api_key\" (key stored as secret named value) or \"managed_identity\" (APIM identity; VALIDATE for Fireworks deployments)."
  type        = string
  default     = "api_key"

  validation {
    condition     = contains(["api_key", "managed_identity"], var.fireworks_backend_auth)
    error_message = "fireworks_backend_auth must be \"api_key\" or \"managed_identity\"."
  }
}

# -----------------------------------------------------------------------------
# Leaderboard (written into ROOT/.env)
# -----------------------------------------------------------------------------
variable "leaderboard_url" {
  description = "Leaderboard base URL provided by your coach (optional)."
  type        = string
  default     = ""
}

variable "leaderboard_key" {
  description = "Shared leaderboard submit key provided by your coach (optional)."
  type        = string
  default     = ""
  sensitive   = true
}
