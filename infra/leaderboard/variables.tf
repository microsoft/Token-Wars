variable "subscription_id" {
  description = "Coach subscription ID; alternatively set ARM_SUBSCRIPTION_ID."
  type        = string
  default     = null
}

variable "location" {
  description = "Azure region for the leaderboard."
  type        = string
  default     = "spaincentral"
}

variable "name" {
  description = "Resource name prefix, independent of participant infrastructure."
  type        = string
  default     = "tw-leaderboard"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,17}$", var.name))
    error_message = "Use 2-18 lowercase letters, digits or hyphens, starting with a letter."
  }
}

variable "container_image" {
  description = "Optional prebuilt image. Leave null to let Terraform create an ACR and build leaderboard/ with az acr build."
  type        = string
  default     = null
}

variable "container_registry_id" {
  description = "Optional private ACR resource ID for a prebuilt container_image. Its login server must match the image registry."
  type        = string
  default     = null
}

variable "admin_key" {
  description = "Coach-only secret for listing/creating boards and scoped export/reset."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.admin_key) >= 16
    error_message = "Use a strong coach-only admin key of at least 16 characters."
  }
}

variable "initial_leaderboard_key" {
  description = "Optional initial event key, registered idempotently at startup. Further keys are created in the coach UI."
  type        = string
  sensitive   = true
  default     = ""

  validation {
    condition     = var.initial_leaderboard_key == "" || can(regex("^[!-~]{1,128}$", var.initial_leaderboard_key))
    error_message = "Use 1-128 printable ASCII characters without spaces, or leave empty."
  }
}
