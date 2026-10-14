<#
.SYNOPSIS
  Token Wars - Fireworks on Foundry prerequisite check (Challenge 2.4 / 3.6).

.DESCRIPTION
  Checks (read-only unless -Register):
    1. Azure CLI installed + logged in, active subscription
    2. Subscription feature Fireworks.EnableDeploy (Microsoft.CognitiveServices) state
    3. Microsoft.CognitiveServices resource provider registration
    4. Your role on the subscription (Owner/Contributor needed to register the feature and deploy)
    5. Region supports Data Zone Standard pay-per-token (US only)
    6. Fireworks models visible in the region (also shows the model "format" Terraform needs)
  Run it during SETUP (the feature can take up to 30 minutes, a deployment up to another 30 minutes).

.EXAMPLE
  ./scripts/check-fireworks-prereqs.ps1
  ./scripts/check-fireworks-prereqs.ps1 -Register
  ./scripts/check-fireworks-prereqs.ps1 -Location westus3 -Subscription <id>
#>
[CmdletBinding()]
param(
  [switch] $Register,
  [string] $Location = "",
  [string] $Subscription = ""
)

$ErrorActionPreference = "Continue"
$FeatureName = "Fireworks.EnableDeploy"
# VALIDATE: namespace. Microsoft Learn only names the preview feature; community samples use Microsoft.CognitiveServices.
# The script falls back to searching all features named *Fireworks* if this lookup fails.
$FeatureNamespace = "Microsoft.CognitiveServices"
$DataZoneRegions = @("eastus", "eastus2", "centralus", "northcentralus", "westus", "westus3")

$script:Blockers = 0
$script:Warnings = 0
$script:NextSteps = New-Object System.Collections.Generic.List[string]
function Ok([string] $m)   { Write-Host "  [ OK ] $m" -ForegroundColor Green }
function Warn([string] $m) { Write-Host "  [WARN] $m" -ForegroundColor Yellow; $script:Warnings++ }
function Fail([string] $m) { Write-Host "  [FAIL] $m" -ForegroundColor Red; $script:Blockers++ }
function Info([string] $m) { Write-Host "         $m" }
function Next([string] $m) { $script:NextSteps.Add($m) }
# Runs the Azure CLI and returns its output, or $null on error (named AzCli: PowerShell names are case-insensitive).
function AzCli { $out = & az @args 2>$null; if ($LASTEXITCODE -ne 0) { return $null }; return $out }

if (-not $Location) {
  $tfvars = Join-Path $PSScriptRoot "..\terraform.tfvars"
  if (Test-Path $tfvars) {
    $match = Select-String -Path $tfvars -Pattern '^\s*fireworks_location\s*=\s*"([^"]*)"' | Select-Object -First 1
    if ($match) { $Location = $match.Matches[0].Groups[1].Value }
  }
}
if (-not $Location) { $Location = "eastus2" }

Write-Host "Token Wars - Fireworks on Foundry prerequisites`n"

# 1. Azure CLI + login ----------------------------------------------------------------------
Write-Host "1) Azure CLI"
if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
  Fail "Azure CLI (az) not found. Install: https://learn.microsoft.com/cli/azure/install-azure-cli"
  exit 1
}
if ($Subscription) {
  & az account set --subscription $Subscription 2>$null
  if ($LASTEXITCODE -ne 0) { Fail "Cannot select subscription $Subscription"; exit 1 }
}
$subId = AzCli account show --query id -o tsv
if (-not $subId) { Fail "Not logged in. Run: az login"; exit 1 }
$subName = AzCli account show --query name -o tsv
Ok "Subscription: $subName ($subId)"

# 2. Feature registration ----------------------------------------------------------------------
Write-Host "2) Subscription feature $FeatureName"
$state = AzCli feature show --namespace $FeatureNamespace --name $FeatureName --query properties.state -o tsv
if (-not $state) {
  $found = AzCli feature list --query "[?contains(name, 'Fireworks')].{name:name, state:properties.state}" -o tsv
  if ($found) {
    Warn "Not found as $FeatureNamespace/$FeatureName; features matching 'Fireworks' in this subscription:"
    $found | ForEach-Object { Info $_ }
    $first = (@($found)[0] -split "`t")
    $FeatureNamespace = ($first[0] -split "/")[0]
    $state = $first[1]
  } else {
    $state = "NotRegistered"
  }
}
switch -Regex ($state) {
  "^Registered$" { Ok "$FeatureNamespace/$FeatureName is Registered" }
  "^(Registering|Pending)$" {
    Fail "$FeatureNamespace/$FeatureName is $state - not usable yet (can take up to 30 minutes)"
    Next "Wait, then re-run this script. If it is still Registering after 30 minutes: az feature unregister ... and register again."
  }
  default {
    if ($Register) {
      Info "registering $FeatureNamespace/$FeatureName ..."
      & az feature register --namespace $FeatureNamespace --name $FeatureName -o none 2>$null
      if ($LASTEXITCODE -eq 0) {
        Fail "Registration requested (state was $state) - not usable until Registered (up to 30 minutes)."
        Next "Re-run this script until the feature shows Registered, then: az provider register --namespace Microsoft.CognitiveServices"
      } else {
        Fail "az feature register failed - you need Owner or Contributor on the subscription."
      }
    } else {
      Fail "$FeatureNamespace/$FeatureName is $state"
      Next "Review the Fireworks terms (Azure portal > Subscriptions > Preview features > $FeatureName), then run: az feature register --namespace $FeatureNamespace --name $FeatureName (or re-run this script with -Register)"
    }
  }
}

# 3. Resource provider -------------------------------------------------------------------------
Write-Host "3) Resource provider Microsoft.CognitiveServices"
$rpState = AzCli provider show --namespace Microsoft.CognitiveServices --query registrationState -o tsv
if ($rpState -eq "Registered") {
  Ok "Microsoft.CognitiveServices is Registered"
  if ($state -eq "Registered" -and $Register) {
    & az provider register --namespace Microsoft.CognitiveServices -o none 2>$null
    if ($LASTEXITCODE -eq 0) { Ok "Re-registered the provider to propagate the feature flag" }
  } elseif ($state -eq "Registered") {
    Next "If Fireworks models do not show up yet, propagate the feature: az provider register --namespace Microsoft.CognitiveServices"
  }
} else {
  if ($Register) {
    & az provider register --namespace Microsoft.CognitiveServices -o none 2>$null
    if ($LASTEXITCODE -eq 0) { Warn "Provider registration requested (state was $rpState)" } else { Fail "az provider register failed (Owner/Contributor needed)" }
  } else {
    Fail "Microsoft.CognitiveServices is $rpState"
    Next "az provider register --namespace Microsoft.CognitiveServices"
  }
}

# 4. Roles ---------------------------------------------------------------------------------------
Write-Host "4) Your roles on the subscription"
$userType = AzCli account show --query user.type -o tsv
$assignee = if ($userType -eq "user") { AzCli ad signed-in-user show --query id -o tsv } else { AzCli account show --query user.name -o tsv }
$roles = @()
if ($assignee) {
  $roles = @(@(AzCli role assignment list --assignee $assignee --scope "/subscriptions/$subId" --include-inherited --include-groups --query "[].roleDefinitionName" -o tsv) | Where-Object { $_ } | Sort-Object -Unique)
}
if ($roles -contains "Owner" -or $roles -contains "Contributor") {
  Ok ("Roles: " + ($roles -join ","))
} else {
  Warn ("Could not confirm Owner/Contributor on the subscription (found: " + $(if ($roles.Count -gt 0) { $roles -join "," } else { "none or not readable" }) + ").")
  Next "Ask a subscription Owner to register the feature, or to grant you Contributor."
}
Info "Deploying Fireworks models needs 'Foundry Owner' (formerly 'Azure AI Owner') on the Foundry project per"
Info "Microsoft Learn; Owner/Contributor is usually sufficient. If terraform apply fails with Forbidden, set"
Info "fireworks_grant_deployer_role = true in terraform.tfvars and apply again (RBAC propagation can take minutes)."

# 5. Region ----------------------------------------------------------------------------------------
Write-Host "5) Region: $Location"
if ($DataZoneRegions -contains $Location.ToLowerInvariant()) {
  Ok "$Location supports Data Zone Standard and Global Standard Fireworks deployments"
} else {
  Info ("$Location is outside the US Data Zone set (" + ($DataZoneRegions -join " ") + ").")
  Info "The default FW-GLM-5.3-Flash uses Global Standard; confirm that offer is listed in step 6."
}
Info "Note: Fireworks on Foundry is currently excluded from the EU Data Boundary (workshop data is synthetic)."

# 6. Model catalog / format --------------------------------------------------------------------------
Write-Host "6) Fireworks models available to this subscription in $Location"
$models = @(@(AzCli cognitiveservices model list --location $Location --query "[?starts_with(model.name, 'FW-')].{name:model.name, format:model.format, version:model.version, sku:model.skus[0].name}" -o tsv) |
  Where-Object { $_ } | Sort-Object -Unique)
if ($models.Count -gt 0) {
  Ok "Found Fireworks models (name / format / version / first SKU):"
  $models | Select-Object -First 40 | ForEach-Object { Info $_ }
  $format = ($models[0] -split "`t")[1]
  if ($format) { Info "-> Terraform: fireworks_model_format = `"$format`" (default in variables.tf: `"Fireworks`")" }
  $names = $models | ForEach-Object { ($_ -split "`t")[0] }
  foreach ($m in @("FW-GLM-5.3-Flash")) {
    if ($names -contains $m) { Ok "$m available" } else { Warn "$m not listed - pick an alternative in fireworks_models" }
  }
} else {
  Warn "No FW-* models listed (feature not active yet, region not supported, or older Azure CLI)."
  Next "After the feature is Registered: az cognitiveservices model list --location $Location --query `"[?starts_with(model.name,'FW-')]`" -o table"
}

Write-Host ""
Write-Host "Summary: $($script:Blockers) blocker(s), $($script:Warnings) warning(s)."
if ($script:NextSteps.Count -gt 0) {
  Write-Host "Next steps:"
  $script:NextSteps | ForEach-Object { Write-Host "  - $_" }
}
if ($script:Blockers -eq 0) {
  Write-Host "Then deploy (from infra/): terraform apply -var 'deploy_fireworks=true'   # up to 30 minutes per deployment"
  exit 0
}
exit 1
