#!/usr/bin/env bash
# Own the Weights – serverless SFT on Microsoft Foundry (coach subscription only, pay-per-token, NO PTU).
#
#   ./finetune.sh preflight   # az login, account/region, data files, base-model id candidates
#   ./finetune.sh upload      # POST {endpoint}/files  (purpose=fine-tune) for train.jsonl + validation.jsonl
#   ./finetune.sh create      # POST {endpoint}/fine_tuning/jobs  (trainingType=GlobalStandard)
#   ./finetune.sh wait        # poll the job until succeeded/failed (prints status + latest event)
#   ./finetune.sh deploy      # ARM PUT …/accounts/<acct>/deployments/<name>  (sku GlobalStandard)
#   ./finetune.sh test        # one chat completion through required APIM
#   ./finetune.sh status      # show state + job + deployment
#   ./finetune.sh delete      # DELETE the deployment → stops the hourly hosting fee
#   ./finetune.sh cleanup-files   # optional: delete the uploaded training/validation files
#   ./finetune.sh all         # preflight → upload → create → wait → deploy → test
#
# Required env: AZ_SUBSCRIPTION_ID, AZ_RESOURCE_GROUP, FOUNDRY_ACCOUNT (Foundry/AIServices account name).
# Optional env (defaults in brackets):
#   FOUNDRY_ENDPOINT [https://$FOUNDRY_ACCOUNT.services.ai.azure.com/openai/v1]   # VALIDATE: new-Foundry domain
#   FOUNDRY_API_KEY  [empty → Entra ID token via `az account get-access-token`, needs Foundry User role]
#   BASE_MODEL [Ministral-3B]   # VALIDATE exact model id: run `preflight` – alternatives gpt-oss-20b, Qwen3-32B
#   TRAINING_TYPE [GlobalStandard]  SUFFIX [bytecart]  SEED [42]  N_EPOCHS [empty = service default]
#   DEPLOYMENT_NAME [bytecart-ft]  DEPLOY_SKU [GlobalStandard]  DEPLOY_CAPACITY [50]   # VALIDATE capacity unit/quota
#   FT_MODEL_FORMAT [auto → az list-models lookup, fallback OpenAI]   FT_MODEL_VERSION [1]   # VALIDATE for open-weight
#   ARM_API_VERSION [2024-10-01]  DATA_DIR [./data next to this script]  POLL_SECONDS [60]
#
# Docs (checked 2026-10-09): learn.microsoft.com/azure/foundry/fine-tuning/overview,
#   …/fine-tuning/deploy-fine-tuned-models, …/fine-tuning/cost-management, …/openai/how-to/fine-tuning (REST pivot).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${DATA_DIR:-$SCRIPT_DIR/data}"
STATE_FILE="$DATA_DIR/.finetune-state"
BASE_MODEL="${BASE_MODEL:-Ministral-3B}"
TRAINING_TYPE="${TRAINING_TYPE:-GlobalStandard}"
SUFFIX="${SUFFIX:-bytecart}"
SEED="${SEED:-42}"
N_EPOCHS="${N_EPOCHS:-}"
DEPLOYMENT_NAME="${DEPLOYMENT_NAME:-bytecart-ft}"
DEPLOY_SKU="${DEPLOY_SKU:-GlobalStandard}"
DEPLOY_CAPACITY="${DEPLOY_CAPACITY:-50}"
FT_MODEL_FORMAT="${FT_MODEL_FORMAT:-auto}"
FT_MODEL_VERSION="${FT_MODEL_VERSION:-1}"
ARM_API_VERSION="${ARM_API_VERSION:-2024-10-01}"
POLL_SECONDS="${POLL_SECONDS:-60}"
PY="${PYTHON:-python3}"

die() { echo "❌ $*" >&2; exit 1; }
info() { echo "▶ $*"; }

need_vars() {
  : "${AZ_SUBSCRIPTION_ID:?set AZ_SUBSCRIPTION_ID}" "${AZ_RESOURCE_GROUP:?set AZ_RESOURCE_GROUP}" "${FOUNDRY_ACCOUNT:?set FOUNDRY_ACCOUNT}"
  [[ "$BASE_MODEL" =~ ^[[:alnum:]][[:alnum:]._:/-]*$ ]] \
    || die "invalid BASE_MODEL '$BASE_MODEL' – use the exact model id from preflight (remove commas, spaces and quotes)"
  ENDPOINT="${FOUNDRY_ENDPOINT:-https://${FOUNDRY_ACCOUNT}.services.ai.azure.com/openai/v1}"
  ENDPOINT="${ENDPOINT%/}"
  [[ -n "${AUTH_HEADER:-}" ]] || resolve_auth
  ARM_DEPLOYMENT_URL="https://management.azure.com/subscriptions/${AZ_SUBSCRIPTION_ID}/resourceGroups/${AZ_RESOURCE_GROUP}/providers/Microsoft.CognitiveServices/accounts/${FOUNDRY_ACCOUNT}/deployments/${DEPLOYMENT_NAME}?api-version=${ARM_API_VERSION}"
}

state_get() { [[ -f "$STATE_FILE" ]] && grep -E "^$1=" "$STATE_FILE" | tail -n1 | cut -d= -f2- || true; }
state_set() {
  mkdir -p "$DATA_DIR"; touch "$STATE_FILE"
  grep -vE "^$1=" "$STATE_FILE" > "$STATE_FILE.tmp" || true
  echo "$1=$2" >> "$STATE_FILE.tmp"; mv "$STATE_FILE.tmp" "$STATE_FILE"
}

# json_get <json-string> <python expression on d>
json_get() {
  printf '%s' "$1" | "$PY" -c \
    'import json,sys; d=json.load(sys.stdin); v=eval(sys.argv[1]); print("" if v is None else v)' "$2"
}

# Resolved once in the main shell (api() runs inside $(...) subshells, which could not cache a token).
resolve_auth() {
  if [[ -n "${FOUNDRY_API_KEY:-}" ]]; then
    AUTH_HEADER="api-key: ${FOUNDRY_API_KEY}"
  else
    local token
    token="$(az account get-access-token --resource https://cognitiveservices.azure.com --query accessToken -o tsv)" \
      || die "could not get an Entra ID token (az login?) – or set FOUNDRY_API_KEY"
    AUTH_HEADER="Authorization: Bearer ${token}"
  fi
}

# api <METHOD> <path> [curl args…] → prints body, fails on HTTP >= 400
api() {
  local method="$1" path="$2"; shift 2
  local out code
  out="$(curl -sS -X "$method" "${ENDPOINT}${path}" -H "$AUTH_HEADER" -w $'\n%{http_code}' "$@")" || die "curl failed for $method $path"
  code="${out##*$'\n'}"; out="${out%$'\n'*}"
  if [[ "$code" -ge 400 ]]; then echo "$out" >&2; die "HTTP $code for $method $path"; fi
  printf '%s' "$out"
}

cmd_preflight() {
  need_vars
  command -v az >/dev/null || die "Azure CLI (az) not found"
  command -v curl >/dev/null || die "curl not found"
  command -v "$PY" >/dev/null || die "python3 not found"
  az account set --subscription "$AZ_SUBSCRIPTION_ID"
  info "Account: $(az cognitiveservices account show -g "$AZ_RESOURCE_GROUP" -n "$FOUNDRY_ACCOUNT" --query '[kind, location, properties.customSubDomainName]' -o tsv | tr '\t' ' ')"
  echo "   (Global training needs a supported project region, e.g. swedencentral – see the overview's region table.)"
  for f in train.jsonl validation.jsonl; do
    [[ -f "$DATA_DIR/$f" ]] || die "$DATA_DIR/$f missing – run: python generate_dataset.py --out \"$DATA_DIR\""
    info "$f: $(wc -l < "$DATA_DIR/$f" | tr -d ' ') examples"
  done
  info "Endpoint: $ENDPOINT   auth: $([[ -n "${FOUNDRY_API_KEY:-}" ]] && echo api-key || echo 'Entra ID token')"
  info "Fine-tunable base models matching ministral|gpt-oss|qwen3|llama-3.3 (VALIDATE: capability field names):"
  api GET /models | "$PY" -c '
import json, re, sys
data = json.load(sys.stdin).get("data", [])
hits = [m for m in data if re.search(r"ministral|gpt-oss|qwen3|llama-3\.3", m.get("id", ""), re.I)]
for m in hits:
    ft = (m.get("capabilities") or {}).get("fine_tune")
    print(f"   {m.get('id')}   fine_tune={ft}")
if not hits:
    print("   (none listed – check the model id in the Foundry portal: Fine-tuning > + Fine-tune model)")
'
  echo "   BASE_MODEL=$BASE_MODEL  TRAINING_TYPE=$TRAINING_TYPE  DEPLOY_SKU=$DEPLOY_SKU (pay-per-token + hourly hosting, no PTU)"
}

cmd_upload() {
  need_vars
  for kind in train validation; do
    local f="$DATA_DIR/$kind.jsonl" resp id status
    [[ -f "$f" ]] || die "$f missing"
    info "Uploading $f"
    resp="$(api POST /files -F purpose=fine-tune -F "file=@${f}")"
    id="$(json_get "$resp" 'd["id"]')"
    state_set "${kind}_file_id" "$id"
    for ((i = 0; i < 60; i++)); do   # files must be processed before a job can use them (VALIDATE status values)
      status="$(json_get "$(api GET "/files/$id")" 'd.get("status")')"
      [[ "$status" == "processed" || -z "$status" ]] && break
      [[ "$status" == "error" ]] && die "file $id failed validation"
      sleep 5
    done
    echo "   $kind file id: $id (status: ${status:-n/a})"
  done
}

cmd_create() {
  need_vars
  local train val body resp job
  train="$(state_get train_file_id)"; val="$(state_get validation_file_id)"
  [[ -n "$train" ]] || die "no training file id – run upload first"
  body="$("$PY" - "$BASE_MODEL" "$train" "$val" "$SUFFIX" "$SEED" "$TRAINING_TYPE" "$N_EPOCHS" <<'PYEOF'
import json, sys
model, train, val, suffix, seed, training_type, epochs = sys.argv[1:8]
body = {"model": model, "training_file": train, "suffix": suffix, "seed": int(seed), "trainingType": training_type}
if val:
    body["validation_file"] = val
if epochs:
    body["method"] = {"type": "supervised", "supervised": {"hyperparameters": {"n_epochs": int(epochs)}}}
print(json.dumps(body))
PYEOF
)"
  info "Creating fine-tuning job: $body"
  resp="$(api POST /fine_tuning/jobs -H "Content-Type: application/json" -d "$body")"
  job="$(json_get "$resp" 'd["id"]')"
  state_set job_id "$job"
  echo "   job id: $job  status: $(json_get "$resp" 'd.get("status")')"
}

cmd_wait() {
  need_vars
  local job resp status model
  job="$(state_get job_id)"; [[ -n "$job" ]] || die "no job id – run create first"
  while true; do
    resp="$(api GET "/fine_tuning/jobs/$job")"
    status="$(json_get "$resp" 'd.get("status")')"
    local event
    event="$(json_get "$(api GET "/fine_tuning/jobs/$job/events?limit=1")" '(d.get("data") or [{}])[0].get("message")')"
    echo "$(date -u +%H:%M:%SZ) status=$status  ${event}"
    case "$status" in
      succeeded)
        model="$(json_get "$resp" 'd.get("fine_tuned_model")')"
        state_set fine_tuned_model "$model"
        echo "✅ fine-tuned model: $model   trained_tokens: $(json_get "$resp" 'd.get("trained_tokens")')"
        return 0 ;;
      failed|cancelled)
        echo "$resp" >&2; die "job $status" ;;
    esac
    sleep "$POLL_SECONDS"
  done
}

cmd_deploy() {
  need_vars
  local model format body state
  model="${FT_MODEL:-$(state_get fine_tuned_model)}"
  [[ -n "$model" ]] || die "no fine-tuned model – run wait first (or set FT_MODEL)"
  format="$FT_MODEL_FORMAT"
  if [[ "$format" == "auto" ]]; then
    # VALIDATE: assumes fine-tuned models are listed by the account's models API with their deployment format.
    format="$(az cognitiveservices account list-models -g "$AZ_RESOURCE_GROUP" -n "$FOUNDRY_ACCOUNT" \
      --query "[?name=='${model}'].format | [0]" -o tsv 2>/dev/null || true)"
    format="${format:-OpenAI}"
  fi
  body="$("$PY" -c 'import json,sys; print(json.dumps({"sku":{"name":sys.argv[1],"capacity":int(sys.argv[2])},"properties":{"model":{"format":sys.argv[3],"name":sys.argv[4],"version":sys.argv[5]}}}))' \
    "$DEPLOY_SKU" "$DEPLOY_CAPACITY" "$format" "$model" "$FT_MODEL_VERSION")"
  info "PUT deployment $DEPLOYMENT_NAME: $body"
  echo "   ⏱️  From now on the deployment bills an HOURLY hosting fee until you run: ./finetune.sh delete"
  az rest --method put --url "$ARM_DEPLOYMENT_URL" --body "$body" --headers "Content-Type=application/json" >/dev/null
  for ((i = 0; i < 120; i++)); do
    state="$(az cognitiveservices account deployment show -g "$AZ_RESOURCE_GROUP" -n "$FOUNDRY_ACCOUNT" \
      --deployment-name "$DEPLOYMENT_NAME" --query properties.provisioningState -o tsv 2>/dev/null || true)"
    echo "$(date -u +%H:%M:%SZ) provisioningState=$state"
    [[ "$state" == "Succeeded" ]] && break
    [[ "$state" == "Failed" ]] && die "deployment failed – check the portal (Models page) and FT_MODEL_FORMAT/DEPLOY_CAPACITY"
    sleep 30
  done
  state_set deployment_name "$DEPLOYMENT_NAME"
  echo "✅ Deployed. Next: python register_custom_model.py --deployment $DEPLOYMENT_NAME --resource $FOUNDRY_ACCOUNT --write-pricing"
}

cmd_test() {
  local gateway gw_url gw_key
  gateway="$("$PY" - "$SCRIPT_DIR" <<'PYEOF'
import json, sys
sys.path.insert(0, sys.argv[1])
from kit_common import find_root, read_json, gateway_credentials
root = find_root()
print(json.dumps(gateway_credentials(root, read_json(root / "shared" / "config" / "models.json"))))
PYEOF
)" || die "required gateway configuration could not be loaded"
  gw_url="$(json_get "$gateway" 'd[0]')"; gw_url="${gw_url%$'\r'}"
  gw_key="$(json_get "$gateway" 'd[1]')"; gw_key="${gw_key%$'\r'}"
  local body
  body="$("$PY" - "$DATA_DIR/validation.jsonl" "$DEPLOYMENT_NAME" <<'PYEOF'
import json, sys
with open(sys.argv[1], encoding="utf-8-sig") as fh:
    ex = json.loads(fh.readline())
msgs = [m for m in ex["messages"] if m["role"] != "assistant"]
print(json.dumps({"model": sys.argv[2], "messages": msgs, "max_tokens": 300, "temperature": 0.2}))
PYEOF
)"
  local resp
  resp="$(ENDPOINT="${gw_url%/}" AUTH_HEADER="api-key: $gw_key" api POST /chat/completions -H "Content-Type: application/json" -d "$body")"
  echo "Question: $(json_get "$body" 'd["messages"][-1]["content"].split("Question: ")[-1]')"
  echo "Answer:   $(json_get "$resp" 'd["choices"][0]["message"]["content"]')"
  echo "Usage:    $(json_get "$resp" 'd.get("usage")')"
}

cmd_status() {
  need_vars
  [[ -f "$STATE_FILE" ]] && cat "$STATE_FILE" || echo "(no state file at $STATE_FILE)"
  local job; job="$(state_get job_id)"
  [[ -z "$job" ]] || echo "job: $(json_get "$(api GET "/fine_tuning/jobs/$job")" 'd.get("status")')"
  az cognitiveservices account deployment show -g "$AZ_RESOURCE_GROUP" -n "$FOUNDRY_ACCOUNT" --deployment-name "$DEPLOYMENT_NAME" \
    --query '{name:name, state:properties.provisioningState, model:properties.model.name, sku:sku.name}' -o table 2>/dev/null \
    || echo "deployment $DEPLOYMENT_NAME: not found (no hosting fee)"
}

cmd_delete() {
  need_vars
  info "Deleting deployment $DEPLOYMENT_NAME (stops the hourly hosting fee; the fine-tuned model itself is kept)"
  az cognitiveservices account deployment delete -g "$AZ_RESOURCE_GROUP" -n "$FOUNDRY_ACCOUNT" --deployment-name "$DEPLOYMENT_NAME"
  echo "✅ Deleted. Also run: python register_custom_model.py --remove --write-pricing"
}

cmd_cleanup_files() {
  need_vars
  for k in train_file_id validation_file_id; do
    local id; id="$(state_get "$k")"
    if [[ -n "$id" ]]; then api DELETE "/files/$id" >/dev/null && echo "deleted $id"; fi
  done
}

case "${1:-}" in
  preflight) cmd_preflight ;;
  upload) cmd_upload ;;
  create) cmd_create ;;
  wait) cmd_wait ;;
  deploy) cmd_deploy ;;
  test) cmd_test ;;
  status) cmd_status ;;
  delete) cmd_delete ;;
  cleanup-files) cmd_cleanup_files ;;
  all) cmd_preflight; cmd_upload; cmd_create; cmd_wait; cmd_deploy; cmd_test ;;
  *) sed -n '2,14p' "$0"; exit 1 ;;
esac
