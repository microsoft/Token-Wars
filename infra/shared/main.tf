# -----------------------------------------------------------------------------
# Token Wars - shared-infrastructure mode (teams WITHOUT their own Azure subscription)
#
#   team app --(team APIM key)--> team APIM /openai/v1/ --(APIM managed identity)--> coach Foundry / Fireworks / Ollama
#   team app --(team judge key)--> team APIM /judge/v1/  --(APIM managed identity)--> coach Foundry "judge" deployment
#
# One resource group + APIM per team, all in the coach subscription. The model deployments are NOT duplicated:
# every team APIM points to the resources of the coach stack in infra/ (read from its local state).
# Run it through deploy_teams.py / set_policy.py (see README.md in this folder).
# -----------------------------------------------------------------------------

data "terraform_remote_state" "coach" {
  backend = "local"
  config = {
    path = var.coach_state_path
  }
}

locals {
  coach     = data.terraform_remote_state.coach.outputs.shared_mode_config
  infra_dir = abspath("${path.root}/..")

  output_dir = coalesce(var.output_dir, abspath("${path.root}/out"))

  # Extra registry keys that are not in the coach state (e.g. `custom` added by register_custom_model.py).
  coach_models_file  = try(jsondecode(file(var.coach_models_json)).models, {})
  coach_models_extra = { for k, m in local.coach_models_file : k => m if k != "judge" && !contains(keys(local.coach.models), k) }

  # A team APIM can only serve an extra model if one of its backends hosts it: the primary Foundry resource
  # (Azure route), the Ollama app, or a Fireworks deployment the policies already route by name.
  unsupported_extra_models = [
    for k, m in local.coach_models_extra : k if !(
      try(regex("^https://([^./]+)\\.", m.base_url)[0], "") == local.coach.primary.subdomain ||
      try(m.base_url == local.coach.selfhosted.base_url && m.deployment == local.coach.selfhosted.model, false) ||
      try(m.base_url == local.coach.fireworks.base_url && contains(local.coach.fireworks.models, m.deployment), false)
    )
  ]

  # Every non-judge model is reached through the team APIM with the team key (backend credentials stay in APIM).
  team_models = {
    for k, m in merge(local.coach_models_extra, local.coach.models) : k => merge(m, {
      api_key_env = "APIM_SUBSCRIPTION_KEY"
      via_gateway = true
    }) if k != "judge"
  }

  policy_files = { for t in var.teams : t => lookup(var.team_policy_files, t, var.default_policy_file) }
  policy_paths = {
    for t, p in local.policy_files : t => (
      startswith(p, "/") || can(regex("^[A-Za-z]:[\\\\/]", p)) ? p : "${local.infra_dir}/${p}"
    )
  }
}

resource "terraform_data" "validate" {
  input = keys(local.coach_models_extra)

  lifecycle {
    precondition {
      condition     = length(local.unsupported_extra_models) == 0
      error_message = "These models.json keys are not hosted by a backend the team APIMs know (primary Foundry, Ollama or a Fireworks deployment): ${join(", ", local.unsupported_extra_models)}. Deploy them on the coach's primary Foundry resource or remove them from ${var.coach_models_json}."
    }
    precondition {
      condition     = length(setsubtract(keys(var.team_policy_files), var.teams)) == 0
      error_message = "team_policy_files has entries for teams that are not in the team list: ${join(", ", setsubtract(keys(var.team_policy_files), var.teams))}."
    }
  }
}

module "team" {
  source   = "./modules/team"
  for_each = toset(var.teams)

  team                       = each.key
  location                   = coalesce(var.location, local.coach.location)
  suffix                     = local.coach.suffix
  tags                       = merge(local.coach.tags, var.tags, { workload = "token-wars-microhack", team = each.key, mode = "shared", coach = local.coach.coach_team, SecurityControl = "Ignore" })
  apim_sku                   = coalesce(var.apim_sku, local.coach.apim_sku)
  apim_publisher_email       = local.coach.apim_publisher_email
  apim_consumers             = local.coach.apim_consumers
  tokens_per_minute          = coalesce(var.tokens_per_minute_per_consumer, local.coach.tokens_per_minute)
  pool_models                = local.coach.pool_models
  judge_deployment           = local.coach.judge_deployment
  primary                    = local.coach.primary
  secondary                  = local.coach.secondary
  fireworks                  = local.coach.fireworks
  selfhosted                 = local.coach.selfhosted
  log_analytics_workspace_id = local.coach.log_analytics_workspace_id
  policy_file                = local.policy_paths[each.key]
  judge_policy_file          = "${path.root}/policies/judge.xml"
  models                     = local.team_models
  judge_model                = local.coach.models.judge
  leaderboard_url            = local.coach.leaderboard_url
  leaderboard_key            = local.coach.leaderboard_key
  output_dir                 = "${local.output_dir}/${each.key}"

  depends_on = [terraform_data.validate]
}
