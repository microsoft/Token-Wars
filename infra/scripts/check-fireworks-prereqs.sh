#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Token Wars - Fireworks on Foundry prerequisite check (Challenge 2.4 / 3.6)
#
# Checks (read-only unless --register):
#   1. Azure CLI installed + logged in, active subscription
#   2. Subscription feature Fireworks.EnableDeploy (Microsoft.CognitiveServices) state
#   3. Microsoft.CognitiveServices resource provider registration
#   4. Your role on the subscription (Owner/Contributor needed to register the feature and deploy)
#   5. Region supports Data Zone Standard pay-per-token (US only)
#   6. Fireworks models visible in the region (also shows the model "format" Terraform needs)
#
# Usage (from infra/):
#   ./scripts/check-fireworks-prereqs.sh                       # check only
#   ./scripts/check-fireworks-prereqs.sh --register            # also register the feature + provider (Owner/Contributor)
#   ./scripts/check-fireworks-prereqs.sh --location westus3 --subscription <id>
#
# Run it during SETUP (the feature can take up to 30 minutes, a deployment up to another 30 minutes).
# -----------------------------------------------------------------------------
set -u

FEATURE_NAME="Fireworks.EnableDeploy"
# VALIDATE: namespace. Microsoft Learn only names the preview feature; community samples use Microsoft.CognitiveServices.
# The script falls back to searching all features named *Fireworks* if this lookup fails.
FEATURE_NAMESPACE="Microsoft.CognitiveServices"
DATA_ZONE_REGIONS="eastus eastus2 centralus northcentralus westus westus3"
REGISTER=0
LOCATION=""
SUBSCRIPTION=""

while [ $# -gt 0 ]; do
  case "$1" in
    --register) REGISTER=1 ;;
    --location) LOCATION="${2:-}"; shift ;;
    --subscription) SUBSCRIPTION="${2:-}"; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TFVARS="$SCRIPT_DIR/../terraform.tfvars"
if [ -z "$LOCATION" ] && [ -f "$TFVARS" ]; then
  LOCATION="$(sed -n 's/^[[:space:]]*fireworks_location[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "$TFVARS" | head -n1)"
fi
LOCATION="${LOCATION:-eastus2}"

ok()   { printf '  [ OK ] %s\n' "$*"; }
warn() { printf '  [WARN] %s\n' "$*"; WARNINGS=$((WARNINGS + 1)); }
fail() { printf '  [FAIL] %s\n' "$*"; BLOCKERS=$((BLOCKERS + 1)); }
BLOCKERS=0
WARNINGS=0
NEXT_STEPS=""
next() { NEXT_STEPS="${NEXT_STEPS}  - $*\n"; }

echo "Token Wars - Fireworks on Foundry prerequisites"
echo

# 1. Azure CLI + login -------------------------------------------------------------
echo "1) Azure CLI"
if ! command -v az >/dev/null 2>&1; then
  fail "Azure CLI (az) not found. Install: https://learn.microsoft.com/cli/azure/install-azure-cli"
  exit 1
fi
if [ -n "$SUBSCRIPTION" ]; then
  az account set --subscription "$SUBSCRIPTION" >/dev/null 2>&1 || { fail "Cannot select subscription $SUBSCRIPTION"; exit 1; }
fi
SUB_ID="$(az account show --query id -o tsv 2>/dev/null)"
if [ -z "$SUB_ID" ]; then
  fail "Not logged in. Run: az login"
  exit 1
fi
ok "Subscription: $(az account show --query name -o tsv) ($SUB_ID)"

# 2. Feature registration -----------------------------------------------------------
echo "2) Subscription feature $FEATURE_NAME"
STATE="$(az feature show --namespace "$FEATURE_NAMESPACE" --name "$FEATURE_NAME" --query properties.state -o tsv 2>/dev/null)"
if [ -z "$STATE" ]; then
  FOUND="$(az feature list --query "[?contains(name, 'Fireworks')].{name:name, state:properties.state}" -o tsv 2>/dev/null)"
  if [ -n "$FOUND" ]; then
    warn "Not found as $FEATURE_NAMESPACE/$FEATURE_NAME; features matching 'Fireworks' in this subscription:"
    printf '%s\n' "$FOUND" | sed 's/^/         /'
    FEATURE_NAMESPACE="$(printf '%s\n' "$FOUND" | head -n1 | cut -f1 | cut -d/ -f1)"
    STATE="$(printf '%s\n' "$FOUND" | head -n1 | cut -f2)"
  else
    STATE="NotRegistered"
  fi
fi
case "$STATE" in
  Registered) ok "$FEATURE_NAMESPACE/$FEATURE_NAME is Registered" ;;
  Registering|Pending)
    fail "$FEATURE_NAMESPACE/$FEATURE_NAME is $STATE - not usable yet (can take up to 30 minutes)"
    next "Wait, then re-run this script. If it is still Registering after 30 minutes: az feature unregister ... and register again." ;;
  *)
    if [ "$REGISTER" -eq 1 ]; then
      echo "         registering $FEATURE_NAMESPACE/$FEATURE_NAME ..."
      if az feature register --namespace "$FEATURE_NAMESPACE" --name "$FEATURE_NAME" -o none 2>/dev/null; then
        fail "Registration requested (state was $STATE) - not usable until Registered (up to 30 minutes)."
        next "Re-run this script until the feature shows Registered, then: az provider register --namespace Microsoft.CognitiveServices"
      else
        fail "az feature register failed - you need Owner or Contributor on the subscription."
      fi
    else
      fail "$FEATURE_NAMESPACE/$FEATURE_NAME is $STATE"
      next "Review the Fireworks terms (Azure portal > Subscriptions > Preview features > $FEATURE_NAME), then run:
      az feature register --namespace $FEATURE_NAMESPACE --name $FEATURE_NAME
      (or re-run this script with --register)"
    fi ;;
esac

# 3. Resource provider ----------------------------------------------------------------
echo "3) Resource provider Microsoft.CognitiveServices"
RP_STATE="$(az provider show --namespace Microsoft.CognitiveServices --query registrationState -o tsv 2>/dev/null)"
if [ "$RP_STATE" = "Registered" ]; then
  ok "Microsoft.CognitiveServices is Registered"
  if [ "$STATE" = "Registered" ] && [ "$REGISTER" -eq 1 ]; then
    az provider register --namespace Microsoft.CognitiveServices -o none 2>/dev/null \
      && ok "Re-registered the provider to propagate the feature flag"
  elif [ "$STATE" = "Registered" ]; then
    next "If Fireworks models do not show up yet, propagate the feature: az provider register --namespace Microsoft.CognitiveServices"
  fi
else
  if [ "$REGISTER" -eq 1 ]; then
    az provider register --namespace Microsoft.CognitiveServices -o none 2>/dev/null \
      && warn "Provider registration requested (state was ${RP_STATE:-unknown})" \
      || fail "az provider register failed (Owner/Contributor needed)"
  else
    fail "Microsoft.CognitiveServices is ${RP_STATE:-unknown}"
    next "az provider register --namespace Microsoft.CognitiveServices"
  fi
fi

# 4. Roles ------------------------------------------------------------------------------
echo "4) Your roles on the subscription"
USER_TYPE="$(az account show --query user.type -o tsv 2>/dev/null)"
if [ "$USER_TYPE" = "user" ]; then
  ASSIGNEE="$(az ad signed-in-user show --query id -o tsv 2>/dev/null)"
else
  ASSIGNEE="$(az account show --query user.name -o tsv 2>/dev/null)"
fi
ROLES=""
if [ -n "$ASSIGNEE" ]; then
  ROLES="$(az role assignment list --assignee "$ASSIGNEE" --scope "/subscriptions/$SUB_ID" --include-inherited --include-groups \
    --query "[].roleDefinitionName" -o tsv 2>/dev/null | sort -u | tr '\n' ',' | sed 's/,$//')"
fi
if printf '%s' "$ROLES" | grep -Eq '(^|,)(Owner|Contributor)(,|$)'; then
  ok "Roles: $ROLES"
else
  warn "Could not confirm Owner/Contributor on the subscription (found: ${ROLES:-none or not readable})."
  next "Ask a subscription Owner to register the feature, or to grant you Contributor."
fi
echo "         Deploying Fireworks models needs 'Foundry Owner' (formerly 'Azure AI Owner') on the Foundry project per"
echo "         Microsoft Learn; Owner/Contributor is usually sufficient. If terraform apply fails with Forbidden, set"
echo "         fireworks_grant_deployer_role = true in terraform.tfvars and apply again (RBAC propagation can take minutes)."

# 5. Region -----------------------------------------------------------------------------
echo "5) Region: $LOCATION"
LOC_LC="$(printf '%s' "$LOCATION" | tr '[:upper:]' '[:lower:]')"
if printf ' %s ' "$DATA_ZONE_REGIONS" | grep -q " $LOC_LC "; then
  ok "$LOCATION supports Data Zone Standard and Global Standard Fireworks deployments"
else
  echo "         $LOCATION is outside the US Data Zone set ($DATA_ZONE_REGIONS)."
  echo "         The default FW-GLM-5.3-Flash uses Global Standard; confirm that offer is listed in step 6."
fi
echo "         Note: Fireworks on Foundry is currently excluded from the EU Data Boundary (workshop data is synthetic)."

# 6. Model catalog / format ---------------------------------------------------------------
echo "6) Fireworks models available to this subscription in $LOCATION"
MODELS="$(az cognitiveservices model list --location "$LOCATION" \
  --query "[?starts_with(model.name, 'FW-')].{name:model.name, format:model.format, version:model.version, sku:model.skus[0].name}" \
  -o tsv 2>/dev/null | sort -u)"
if [ -n "$MODELS" ]; then
  ok "Found Fireworks models (name / format / version / first SKU):"
  printf '%s\n' "$MODELS" | head -n 40 | sed 's/^/         /'
  FORMAT="$(printf '%s\n' "$MODELS" | head -n1 | cut -f2)"
  [ -n "$FORMAT" ] && echo "         -> Terraform: fireworks_model_format = \"$FORMAT\" (default in variables.tf: \"Fireworks\")"
  for m in FW-GLM-5.3-Flash; do
    if printf '%s\n' "$MODELS" | cut -f1 | grep -qx "$m"; then ok "$m available"; else warn "$m not listed - pick an alternative in fireworks_models"; fi
  done
else
  warn "No FW-* models listed (feature not active yet, region not supported, or older Azure CLI)."
  next "After the feature is Registered: az cognitiveservices model list --location $LOCATION --query \"[?starts_with(model.name,'FW-')]\" -o table"
fi

echo
echo "Summary: $BLOCKERS blocker(s), $WARNINGS warning(s)."
if [ -n "$NEXT_STEPS" ]; then
  echo "Next steps:"
  printf '%b' "$NEXT_STEPS"
fi
if [ "$BLOCKERS" -eq 0 ]; then
  echo "Then deploy (from infra/): terraform apply -var 'deploy_fireworks=true'   # up to 30 minutes per deployment"
  exit 0
fi
exit 1
