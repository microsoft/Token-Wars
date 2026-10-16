output "teams" {
  description = "Per-team gateway resources and generated files."
  value = {
    for t, m in module.team : t => {
      resource_group = m.resource_group_name
      apim_name      = m.apim_name
      gateway_url    = m.gateway_url
      judge_url      = m.judge_url
      policy_file    = local.policy_paths[t]
      dotenv         = m.dotenv_file
      models_json    = m.models_json_file
    }
  }
}

output "coach_resource_group_hint" {
  description = "Shared model resources live in the coach stack (infra/); team RGs only contain APIM + App Insights."
  value       = "Coach Foundry: ${local.coach.primary.subdomain}; team files: ${local.output_dir}"
}
