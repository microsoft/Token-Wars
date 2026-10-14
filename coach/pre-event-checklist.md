# Pre-event Checklist — Token Wars

Every team brings **its own Azure subscription**; the coach uses a **separate** subscription for the leaderboard and
the "Own the Weights" demo. Everything is **pay-as-you-go** (per token) — nothing in the kit requires PTU.

Related: [coach-guide.md](coach-guide.md) · [troubleshooting.md](troubleshooting.md) · [scoring.md](scoring.md) ·
[own-the-weights/README.md](own-the-weights/README.md) · repository [README](../README.md).

---

## Part A — Each team (own subscription)

### A1. Access and roles (T-7 days)

- [ ] An Azure subscription for the team (sandbox / pay-as-you-go is fine) with **no Azure Policy** that blocks:
      local auth on Foundry resources (`disableLocalAuth` — the apps use API keys), public network access, or the
      regions `swedencentral` (and a US region such as `eastus2` if you want the Fireworks Arena).
- [ ] The person running Terraform has **Owner**, or **Contributor + User Access Administrator**, on the subscription:
  - role assignments are needed for APIM's managed identity (and optionally the Fireworks deployer role);
  - azurerm 5.x registers the resource providers `Microsoft.CognitiveServices`, `Microsoft.ApiManagement`,
    `Microsoft.OperationalInsights`, `Microsoft.Insights` and `Microsoft.App` itself — this needs Owner/Contributor.
- [ ] Optional, Fireworks: registering the feature needs Subscription **Owner/Contributor**; deploying Fireworks models
      needs **Foundry Owner** (formerly Azure AI Owner) on the Foundry project — Owner/Contributor usually works, and
      Terraform can assign the role (`fireworks_grant_deployer_role = true`).

### A2. Tools (T-7 days)

| Tool | Version | Check |
|---|---|---|
| Azure CLI | current release (`az upgrade`) | `az version` |
| Terraform | ≥ 1.6 | `terraform version` |
| Azure Developer CLI (`azd`) | **not needed** — the kit deploys with Terraform | — |
| Python **or** .NET | Python 3.10+ with `requests` **or** .NET 8 SDK (or newer) | `python --version` / `dotnet --version` |
| Git | any | `git --version` |
| PowerShell 7+ (Windows, optional) | for the `.ps1` scripts | `pwsh --version` |

```bash
git clone <repo-url> token-wars-microhack && cd token-wars-microhack
az login
az account set --subscription <your-subscription-id>
export ARM_SUBSCRIPTION_ID=$(az account show --query id -o tsv)   # PowerShell: $env:ARM_SUBSCRIPTION_ID = (az account show --query id -o tsv)
```

If you reuse an older checkout with a `.terraform.lock.hcl` from azurerm 4.x, run `terraform init -upgrade` (the kit now
pins azurerm `~> 5.8`).

### A3. Quota (T-7 days)

Default region `swedencentral`, GlobalStandard. Defaults per team (TPM in thousands): gpt-5.6-sol **800**, gpt-5.6-terra
**800**, gpt-5.6-luna **800**, gpt-5.5 **800** (separate `judge` deployment), text-embedding-3-small **150**,
Llama-3.3-70B-Instruct **100**.

```bash
az cognitiveservices model list --location swedencentral \
  --query "[?contains(model.name,'gpt-5.6-') || model.name=='gpt-5.5' || contains(model.name,'text-embedding-3-small') || contains(model.name,'Llama-3.3')].{name:model.name, version:model.version, format:model.format}" -o table
az cognitiveservices usage list --location swedencentral -o table      # current usage vs limit per model + SKU
```

- [ ] gpt-5.6-sol (2026-07-09), gpt-5.6-terra and gpt-5.6-luna (2026-07-09) are listed and have enough GlobalStandard quota.
- [ ] gpt-5.5 (2026-04-24) is available with 800K TPM quota for `judge`; it does not share Terra's model allocation.
- [ ] Coaches use the same pinned GPT-5.5 judge, prompt and settings for every team, check a human-graded sample,
      and repeat baselines/comparisons after migration. Do not mix scores produced by different judges.
- [ ] text-embedding-3-small has quota (if GlobalStandard is not offered: `embedding_model = { sku = "Standard" }`).
- [ ] Llama-3.3-70B-Instruct is available (serverless, format `Meta`).
- [ ] Less quota? Lower `capacity` per model in `terraform.tfvars` (and `"concurrency"` in `strategy.json`) or request
      more in the Foundry portal (*Quota*). Tell your coach.

### A4. Optional: Fireworks on Foundry (Challenge 2.4 / 3.6) — at least 2 days before

```bash
cd infra
./scripts/check-fireworks-prereqs.sh --register     # PowerShell: pwsh ./scripts/check-fireworks-prereqs.ps1 -Register
./scripts/check-fireworks-prereqs.sh                # repeat until "0 blockers" (the feature can take up to 30 min)
```

- [ ] Feature `Microsoft.CognitiveServices/Fireworks.EnableDeploy` is **Registered** ("Registering" still counts as a
      blocker), and the `Microsoft.CognitiveServices` provider was re-registered (the script does it with `--register`).
- [ ] `fireworks_location` exposes `FW-GLM-5.3-Flash` with Global Standard quota (default `eastus2`).
- [ ] Step 6 of the script lists `FW-GLM-5.3-Flash` (Global Standard). Note the
      *format* column — if it is not `Fireworks`, set `fireworks_model_format` accordingly.
- [ ] Fireworks quota: default 10M TPM per region per pool (shared by all Fireworks models). More: aka.ms/fireworks-quota.
- [ ] Understood the current limitations: Data Zone Standard is US-only, Fireworks is **excluded from the EU Data
      Boundary** (the workshop uses synthetic data), chat completions only, adaptive rate limits (429s).

### A5. Deploy the day before (T-1)

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars        # team_name, leaderboard_url, leaderboard_key (from your coach)
#   optional: deploy_fireworks = true               (only after A4 reports 0 blockers)
terraform init                                      # or: terraform init -upgrade
terraform apply                                     # 10–20 min; + up to 30 min per Fireworks deployment
terraform output foundry_portal_hint
terraform output next_steps
```

- [ ] `terraform apply` finished; `.env` and `shared/config/models.json` were written.
- [ ] Required APIM is deployed; every non-judge entry has `via_gateway: true` and both strategies use
      `use_gateway: true`. Only the independent judge is direct. Fireworks and Ollama routes are present when enabled.
- [ ] Application Insights **Provider Tokens** groups consumption by lowercase `team` and `provider`;
      the reported final provider/model is correct. Do not sum this with native LLM token metrics.
- [ ] Existing checkout: migrate tier overrides/registries to `premium` / `balanced` / `economy` using the
      [tier migration instructions](../README.md#provider-neutral-model-tiers). Confirm the old infrastructure was
      destroyed using its original configuration before a fresh deployment; no automatic state migration is provided.
- [ ] `doctor` is green for every chat model (`fw` too if enabled):
      `cd python/starter && python -m tokenwars doctor` or `cd dotnet/starter/TokenWars && dotnet run -- doctor`.
- [ ] One quick real call works: `run --limit 20 --no-judge` (≈ $0.80 on the baseline). Don't submit it.
- [ ] 401s on `*.services.ai.azure.com`? Set `ai_endpoint_style = "openai"`, re-apply, run `doctor` again.
- [ ] Leave the deployment running overnight (APIM BasicV2 ≈ $0.20–0.25/h). Pay-per-token deployments cost nothing idle.

### A6. After the event

- [ ] `cd infra && terraform destroy` — deletes the resource group with the Foundry resources (incl. Fireworks), APIM,
      Container Apps and logs. The Fireworks feature registration may stay; it costs nothing.

---

## Part B — Coach (separate coach subscription)

| When | What | Done |
|---|---|---|
| T-14 d | Send Part A to every team; collect team names and subscription owners; agree the region. | [ ] |
| T-14 d | Decide whether the Fireworks Arena (2.4) and Failover (3.6) are offered; tell teams to start A4 early. | [ ] |
| T-7 d | **Dry run in a fresh subscription** (no registered providers, no Fireworks feature) — full flow incl. Fireworks and the multi-provider policy; work through the VALIDATE list in [coach-guide.md](coach-guide.md) section 11. | [ ] |
| T-7 d | **Confirm the Fireworks per-token model list** (offers can retire with 15 days' notice) and **prices** (Azure pricing calculator); update `fireworks_models` defaults / `shared/config/pricing.json`, set `"verified": true` only for confirmed prices, push to the repo. | [ ] |
| T-7 d | Update all other prices in `pricing.json` to current list prices; record dry-run numbers for the expected-numbers table. | [ ] |
| T-7 d | "Own the Weights": coach subscription, region `swedencentral`, Foundry Owner role, fine-tuned Global Standard quota; deploy the Token Wars infra with `team_name = "coach"` ([own-the-weights/README.md](own-the-weights/README.md) section 5). | [ ] |
| T-3 d | Check every team's A1–A4 status; help teams with quota or policy problems. | [ ] |
| T-2 d | "Own the Weights": `generate_dataset.py --mock`, then the real run; review `manifest.json` and leakage rows. | [ ] |
| T-1 d | **Deploy the leaderboard** ([coach-guide.md](coach-guide.md) section 8, `az containerapp up` + 1 replica); note URL + submit key; submit one real test run; reset the board. | [ ] |
| T-1 d | **"Own the Weights" training**: `finetune all` (train + deploy), `register_custom_model.py --write-pricing`, `doctor`, dry-run `compare --models balanced,premium,custom`, save the JSON as a backup slide. Hosting fee starts now. | [ ] |
| T-1 d | Confirm every team ran A5 (`terraform apply` + `doctor`). | [ ] |
| T-1 h | Leaderboard on the big screen, coach docs open, `custom` reachable in `doctor`. | [ ] |
| Event | Announce at kickoff: Fireworks limitations and unverified prices; the `custom` demo model is not eligible for the leaderboard. | [ ] |
| **After** | **Cost cleanup:** `finetune delete` + `finetune status` (no deployment → no hosting fee), `register_custom_model.py --remove --write-pricing`, `git checkout shared/config/pricing.json`; export results (`GET /api/submissions`) and delete the leaderboard app; `terraform destroy` in the coach subscription; remind teams to `terraform destroy`. | [ ] |
| After +1 d | Check Cost Management in the coach subscription for anything still billing (APIM, Container Apps, fine-tuned deployment). | [ ] |
