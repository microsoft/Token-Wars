# Token Wars: Build, Route & Optimize Your AI Stack

A 4-hour Azure GenAI **cost-optimisation microhack** for digital-native teams.

**ByteCart** is a fictional, fast-growing EU e-commerce marketplace. Its GenAI *Support Copilot* answers customer
questions — and it works, but it is **expensive**: it always calls the premium model, stuffs the whole knowledge base and
the whole orders database into every prompt, puts dynamic data at the top of the system prompt (so prompt caching never
kicks in), has no output limits and no caching.

Your mission: **cut the cost per successful answer** while keeping answer quality above the bar (≥ 85 % of the
100 workload questions judged correct by an LLM judge). The cheapest valid team wins the leaderboard.

| Time | Block |
|---|---|
| 0:00 | Kickoff — "Enter the Arena" (deploy, run the baseline) |
| 0:30 | Challenge 1 — "The Token Diet" (prompt, retrieval, output control, caching) |
| 1:30 | Break |
| 1:40 | Challenge 2 — "Bring Your Own Model" (open-weight & self-hosted models, compare; optional 2.4 *Fireworks Arena*) |
| 2:40 | Challenge 3 — "Route & Rule" (routing, escalation, retries, AI gateway policies; stretch 3.6 *Multi-Provider Failover*) |
| 3:30 | Final Showdown & Debrief |

Step-by-step instructions: open [`docs/index.html`](docs/index.html) in a browser.
Each TODO in the [participant guide](docs/index.html#ch1) has an initially collapsed **GitHub Copilot Agent prompt**
after its hint, plus a collapsed **Solution** after the result check. Prompts and solutions are tailored to the selected
Python/.NET track; solutions include exact starter-file locations, copyable replacements and separate optional extras.
Before the event, every team works through [`coach/pre-event-checklist.md`](coach/pre-event-checklist.md).

## Principles

- **Pay-as-you-go first.** Every model in the kit is billed per token (Global / Data Zone Standard). Nothing in the
  kit requires provisioned throughput (PTU); PTU only appears as a "for reference" line in the coach's break-even demo.
- **Each team brings its own Azure subscription.** Quota, feature registrations and costs stay isolated per team.
  The coach uses a **separate** subscription for the leaderboard and for demos that do not fit into 4 hours
  ("Own the Weights" fine-tuning). Exception: teams without a subscription get a per-team gateway on the coach's
  shared models ([`infra/shared`](infra/shared/README.md)).
- **Optional extras stay optional.** Fireworks on Foundry (Challenge 2.4) and multi-provider failover (Challenge 3.6)
  are off by default (`deploy_fireworks = false`); the core challenges work without them.

## Provider-neutral model tiers

| Tier | App key | Terraform variable | Current default |
|---|---|---|---|
| Premium | `premium` | `premium_model` | GPT-5.6 Sol |
| Balanced | `balanced` | `balanced_model` | GPT-5.6 Terra |
| Economy | `economy` | `economy_model` | GPT-5.6 Luna |

These are capability/cost roles, not provider or product names. Routing uses Economy for SIMPLE, Balanced for
STANDARD and Premium for COMPLEX; the classifier uses Economy and escalation follows
`economy → balanced → premium`. Keep the independent GPT-5.5 `judge` unchanged.

To substitute a model, configure its real catalog name, publisher `format`, `pricing_key` and request settings in
the corresponding Terraform variable. For another OpenAI-compatible endpoint, keep these app keys in
`models.json` and configure its `base_url`, `api_key_env`, token parameter, temperature support and extra body/headers.
All inference entries must use `via_gateway: true`; configure an APIM backend and model-routing rule for any
external resource/provider not served by the built-in routes. Only the configured judge uses `via_gateway: false`.
Update pricing
with your coach and re-measure quality; the tier name does not guarantee capability or price.

**Existing checkouts:** rename `frontier_model`, `mini_model` and `nano_model` overrides to
`premium_model`, `balanced_model` and `economy_model` in your own tfvars/automation, and use the new app keys in
custom strategies, compare commands and registries. This update expects a fresh infrastructure deployment, not an
in-place state migration. Destroy an existing stack using its original configuration before deploying the updated
stack, and review `terraform plan` before applying. Physical model/deployment names, prices and capacity 800 remain
unchanged by the tier rename. Applying regenerates `models.json`; for a local registry migration, rename only its
tier keys and preserve every model entry's settings. Historical result files keep the keys used at the time and
must not be rewritten.
Coach automation should also use `--premium-key` / `--balanced-key` for the break-even calculator and read its
`breakeven_vs_balanced_requests_per_month` JSON field.

## Repository map

```
.
├── README.md                 you are here
├── .env.example              template; terraform writes the real .env (git-ignored)
├── infra/                    Terraform: Microsoft Foundry resource + project + model deployments, APIM AI gateway,
│   │                         monitoring, optional Ollama on Container Apps, optional secondary region
│   ├── fireworks.tf          optional Fireworks on Foundry deployments (Challenge 2.4, deploy_fireworks = true)
│   ├── scripts/              check-fireworks-prereqs.sh / .ps1 (feature registration, roles, region, model list)
│   ├── shared/               coach-only exception for teams without a subscription: one APIM per team in front of
│   │                         the coach's models (deploy_teams.py, set_policy.py)
│   └── policies/             APIM policy templates: ai-gateway-starter.xml (TODO 3.5) / ai-gateway-solution.xml /
│                             ai-gateway-multiprovider.xml (Challenge 3.6 stretch: failover to Fireworks)
├── shared/
│   ├── knowledge-base/       ByteCart help-center articles (10 Markdown docs, ~7.8k tokens)
│   ├── data/orders.json      60 orders of 20 customers (scenario date 2026-09-15)
│   ├── data/workload.jsonl   the 100 customer questions you are scored on (+ ground truth for the judge only)
│   ├── config/               pricing.json, scoring.json, models.example.json (models.json is generated)
│   └── prompts/              system-baseline.md (the wasteful baseline) and judge.md
├── python/starter|solution   Python app (requests only)       -> python -m tokenwars ...
├── dotnet/starter|solution   .NET 8 app (no NuGet packages)    -> dotnet run -- ...
├── leaderboard/              stdlib-only leaderboard server + Dockerfile (for coaches)
├── coach/                    coach guide, scoring rules, troubleshooting
│   ├── pre-event-checklist.md  what each team and the coach must do before the event
│   └── own-the-weights/      coach-only demo: fine-tune a small open-weight model, compare, break-even (coach subscription)
└── docs/index.html           visual step-by-step instructions
```

## Prerequisites

Full list with commands and timing: [`coach/pre-event-checklist.md`](coach/pre-event-checklist.md).

- **Your own Azure subscription** (one per team) with permission to create resource groups, Microsoft Foundry
  resources and deployments, API Management and role assignments: **Owner**, or **Contributor + User Access
  Administrator**. Owner/Contributor is also needed because azurerm 5.x registers the required resource providers
  (`Microsoft.CognitiveServices`, `Microsoft.ApiManagement`, `Microsoft.OperationalInsights`, `Microsoft.Insights`,
  `Microsoft.App`) itself.
- Model quota in your region (default `swedencentral`) for `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`,
  `gpt-5.5` (independent judge), `text-embedding-3-small` and `Llama-3.3-70B-Instruct` (GlobalStandard). Check with
  `az cognitiveservices model list --location swedencentral -o table`, `az cognitiveservices usage list --location swedencentral -o table`
  and the Foundry portal's *Quota* page.
- [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli), [Terraform ≥ 1.6](https://developer.hashicorp.com/terraform/install)
  (the Azure Developer CLI `azd` is not needed).
- **Either** Python 3.10+ (`pip install requests`) **or** .NET 8 SDK (or newer).
- Optional, Challenge 2.4 / 3.6 only: subscription feature `Fireworks.EnableDeploy` registered (up to 30 min) and a
  US region for Fireworks — run `infra/scripts/check-fireworks-prereqs.sh --register` (PowerShell:
  `./scripts/check-fireworks-prereqs.ps1 -Register`) **at least a day before** the event.

> [!WARNING]
> **If you don't have an Azure subscription**, skip the deployment steps (and the subscription, quota and Fireworks
> prerequisites above). Your coach runs the shared-infrastructure mode ([`infra/shared`](infra/shared/README.md)):
> one API Management gateway per team in front of the coach's model deployments. You receive two files from your
> coach: copy `.env` to the repo root and `models.json` to `shared/config/models.json`, then start at step 3
> (`doctor`). Everything goes through **your** APIM, including the judge (separate `JUDGE_API_KEY`); you never get a
> Foundry or Fireworks key. For Challenge 3.5 / 3.6 you edit `infra/policies/ai-gateway-starter.xml` as usual and
> send the file to your coach, who applies it to your gateway (you cannot run `terraform apply`). Model quota, the
> judge and the prompt cache are shared with other teams, and Azure portal steps (Foundry portal, App Insights,
> lowering capacity for 3.6) are shown by the coach.

## Quick start

```bash
# 1. Sign in
az login
export ARM_SUBSCRIPTION_ID=$(az account show --query id -o tsv)      # PowerShell: $env:ARM_SUBSCRIPTION_ID = (az account show --query id -o tsv)

# 2. Deploy (10-20 min; APIM is the slowest part)
cd infra
cp terraform.tfvars.example terraform.tfvars                          # set team_name (+ leaderboard_url/key from your coach)
terraform init                                                        # existing checkout with an old lock file: terraform init -upgrade
terraform apply
#   -> writes ../.env and ../shared/config/models.json
terraform output foundry_portal_hint                                 # how to find your project in https://ai.azure.com

# 3a. Python
cd ../python/starter
pip install -r requirements.txt
python -m tokenwars doctor                  # checks config + test calls (64-token chat output budget)
python -m tokenwars run --limit 20          # quick baseline
python -m tokenwars run --submit            # full scored baseline -> leaderboard

# 3b. .NET
cd ../dotnet/starter/TokenWars
dotnet run -- doctor
dotnet run -- run --limit 20
dotnet run -- run --submit
```

No Azure yet? Every command works offline with `--mock` (or `TOKENWARS_MOCK=1`): no HTTP calls, simulated token counts.

Then work through the challenges: flip flags in `strategy.json`, implement the `TODO x.y` stubs, re-run, and watch
your **cost per successful answer** drop. The `solution/` folders contain the reference implementation.

## Useful commands

| Action | Python (`python/<variant>/`) | .NET (`dotnet/<variant>/TokenWars/`) |
|---|---|---|
| Full scored run | `python -m tokenwars run` | `dotnet run -- run` |
| Quick run | `python -m tokenwars run --limit 20` | `dotnet run -- run --limit 20` |
| Submit to leaderboard | `python -m tokenwars run --submit` | `dotnet run -- run --submit` |
| Skip judging | `python -m tokenwars run --no-judge` | `dotnet run -- run --no-judge` |
| Ask one question | `python -m tokenwars ask "Can I return shoes?" --customer C1001` | `dotnet run -- ask "Can I return shoes?" --customer C1001` |
| Compare models (Ch. 2) | `python -m tokenwars compare --models premium,balanced,economy,open` | `dotnet run -- compare --models premium,balanced,economy,open` |
| Fireworks Arena (Ch. 2.4, optional) | `python -m tokenwars compare --models balanced,premium,fw` | `dotnet run -- compare --models balanced,premium,fw` |
| Check setup | `python -m tokenwars doctor` | `dotnet run -- doctor` |

## Microsoft Foundry

Terraform uses the **new Microsoft Foundry** resource model: one Foundry resource (`azurerm_cognitive_account`, kind
`AIServices`, `project_management_enabled = true`, custom subdomain, system-assigned identity) plus a Foundry
**project** `proj-tokenwars` (display name "Token Wars – <team>"). Model deployments live on the resource; there
are no hub or ML workspace resources. Open the project in the new Foundry portal at <https://ai.azure.com>
(`terraform output foundry_portal_hint`).

- The apps call the OpenAI-compatible v1 API through **required APIM**, except the independent judge.
  By default the direct judge/backend information in `models.json` uses
  `https://<subdomain>.services.ai.azure.com/openai/v1/` (`ai_endpoint_style = "foundry"`). If you get 401s on that
  host, set `ai_endpoint_style = "openai"` (→ `https://<subdomain>.openai.azure.com/openai/v1/`) and re-apply.
- Every policy routes by the request's physical `model`: configured Fireworks deployments go to Fireworks,
  the configured Ollama model goes to its self-hosted backend, and other models go to Azure OpenAI.
  Azure uses managed identity at `https://<subdomain>.openai.azure.com/openai`; Fireworks uses its backend
  credentials (API key by default), and the workshop Ollama backend is anonymous.
- The provider is pinned to **azurerm `~> 5.8`** (4.x ended with 4.81.0). If your checkout still has a lock file from
  azurerm 4.x, run `terraform init -upgrade`. Fallback for teams that must stay on 4.x: see the comment in
  `infra/providers.tf`.
- Upgrading an existing deployment: switching `project_management_enabled` on is an in-place update and the project is
  added; the resource and its deployments are not recreated.

Useful outputs: `foundry_project_name`, `foundry_project_endpoint`, `foundry_project_endpoints`, `foundry_portal_hint`,
`ai_endpoint_style`, `ai_base_url`; with Fireworks also `fireworks_account_name`, `fireworks_base_url`,
`fireworks_deployments`, `fireworks_project_endpoint`, `apim_failover_model_map`. `terraform output next_steps`
prints what to run next.

## Infrastructure switches (`infra/terraform.tfvars`)

| Variable | Default | What it does |
|---|---|---|
| `foundry_project_name` | `proj-tokenwars` | name of the Foundry project |
| `ai_endpoint_style` | `foundry` | host written to `models.json`: `foundry` (`*.services.ai.azure.com`) or `openai` (`*.openai.azure.com`, fallback) |
| `deploy_apim` | `true` | required API Management (BasicV2) multi-provider gateway; `false` is rejected |
| `apim_policy_file` | `policies/ai-gateway-starter.xml` | policy template applied to the `openai` API (TODO 3.5; `ai-gateway-multiprovider.xml` for 3.6) |
| `deploy_selfhosted_model` | `false` | Ollama (`llama3.2:3b`) on Azure Container Apps (CPU) → models.json key `selfhosted` |
| `deploy_secondary_region` | `false` | second Foundry resource + APIM backend pool for the failover demo |
| `deploy_fireworks` | `false` | Challenge 2.4: second Foundry resource + project in `fireworks_location` (default `eastus2`) with one Fireworks deployment → models.json key `fw`, `.env` `FIREWORKS_AI_API_KEY` |
| `fireworks_models` | `fw` = `FW-GLM-5.3-Flash` | GlobalStandard pay-per-token by default (no PTU); alternatives listed in `terraform.tfvars.example` |
| `fireworks_grant_deployer_role` | `false` | assigns `fireworks_deployer_role` (`Foundry Owner`) on the Fireworks project if deployment fails with Forbidden |
| `fireworks_backend_auth` / `failover_model_map` | `api_key` / balanced,premium→fw | Fireworks auth for all policies / Challenge 3.6 Azure→Fireworks failover mapping |
| `*_model` | GPT-5.6 answer tiers, GPT-5.5 judge, text-embedding-3-small, Llama 3.3 70B | model name / version / SKU / capacity (TPM ×1000) / pricing key / `max_tokens_param` / `supports_temperature` / `extra_body` / `extra_headers` |

Re-run `terraform apply` after every change; it regenerates `.env` and `shared/config/models.json`.

### Models

| Key | Default deployment | Notes |
|---|---|---|
| `premium` | `gpt-5.6-sol` (2026-07-09) | reasoning-capable; written with `max_completion_tokens`, no `temperature`, `reasoning_effort: none` |
| `balanced` | `gpt-5.6-terra` (2026-07-09) | same settings |
| `economy` | `gpt-5.6-luna` (2026-07-09) | same settings; also the classifier router |
| `judge` | `judge` → `gpt-5.5` (2026-04-24) | independent evaluator; not part of the score; bypasses the gateway |
| `embedding` | `text-embedding-3-small` | semantic cache / embedding retrieval |
| `open` | `Llama-3.3-70B-Instruct` | serverless open-weight model, `max_tokens`, temperature supported |
| `selfhosted` | `llama3.2:3b` (Ollama) | optional, CPU on Container Apps; routed through APIM |
| `fw` | `FW-GLM-5.3-Flash` | optional (Challenge 2.4), Fireworks on Foundry, Global Standard pay-per-token, through APIM (`via_gateway: true`), `prompt_cache_key` in `extra_body` |
| `custom` | fine-tuned model | coach demo only ("Own the Weights", coach subscription); has an informational `hourly_cost_usd` |

The three GPT-5.6 answer deployments and the independent GPT-5.5 `judge` default to capacity **800** (800K TPM each).
The optional secondary region uses capacity **800** per chat deployment too; embeddings and open-weight models
keep their existing capacities. Terra needs 800K TPM for `balanced`; GPT-5.5 needs a separate 800K TPM allocation for `judge`.

**Why GPT-5.5 for the judge:** the challenges optimise GPT-5.6 answer tiers, so a separate premium evaluator
avoids using Terra to grade Terra's own answers. GPT-5.5 fits the existing short reference-answer/JSON grading
flow; GPT-6.1 Sol is an alternative to evaluate, not a requirement for these challenges. A different model does
not guarantee unbiased grading: coaches must calibrate it against a few human-reviewed answers in the dry run.
The judge keeps `max_completion_tokens`, omitted temperature and `reasoning_effort: none`.

Challenges 1–3, answer routing and the ≥85% quality bar are unchanged. The judge bypasses APIM and is not a
failover target; its cost remains `judge_cost_usd`, outside the leaderboard score. Use the same pinned judge,
prompt and settings for all teams throughout the event. Changing the evaluator can change pass rates:
repeat the baseline and scored comparisons, and do not rank new scores against old-judge runs.

For an existing deployment, run `terraform plan` and review the model replacements, then `terraform apply` from
`infra/`. Terraform regenerates `shared/config/models.json` only after the new deployments exist; until then,
the generated registry still points to your current deployments. Do not manually rewrite Terraform state or old
run results. Re-run `doctor` and the baseline after applying, since model quality and latency must be remeasured.

### Required gateway and provider telemetry

`use_gateway` defaults to `true` in both tracks and cannot be disabled for real inference. All non-judge
models, including Fireworks, Ollama, embeddings and custom models, require `via_gateway: true`, a gateway
URL and an APIM subscription key. Missing configuration fails explicitly; there is no direct-call fallback.
Offline `--mock` still needs no gateway or credentials. The independent configured judge is the only direct
exception and must have `via_gateway: false`; a missing judge does not fall back to an answer model.

Existing configuration: redeploy from the updated templates to regenerate `.env` and `models.json`, retain
`deploy_apim = true`, and change old strategy files to `"use_gateway": true`. Do not just change an external
model's `base_url`: APIM needs its backend and physical-model route too. Credential overrides in inference
`extra_headers` are rejected; affinity headers remain supported. Deployment names must be distinct across
providers. APIM routing does not make public backend endpoints private.

Routing, response headers (`x-tokenwars-provider`, `x-tokenwars-model`) and team/provider token metrics are
already built into **all three policies**, including the starter. TODO 3.5 adds chat budgets, enriches
telemetry with API/model dimensions, and adds Azure pool/retry resilience. TODO 3.6 adds **failover only**.
The native LLM policies apply to chat completions; embeddings also pass through APIM and contribute to the
outbound metric, but not to the chat-only token limiter.
Only the three answer-tier deployments use the Azure regional pool; embeddings, open and custom models
stay on the primary backend because the secondary resource does not deploy them.

For the dashboard, use the outbound **Provider Tokens** metric, attributed to the final serving provider:
`team` is the APIM subscription display name, so each team should use its own subscription key.

```kusto
customMetrics
| where name == "Provider Tokens"
| extend team = tostring(customDimensions["team"]),
         provider = tostring(customDimensions["provider"])
| summarize tokens = sum(value) by team, provider, bin(timestamp, 1h)
| order by timestamp asc
```

Provider values are `azure-openai`, `fireworks` and `selfhosted`. Native LLM token metrics describe the
requested route, while the outbound counter reflects a rewritten failover model/provider. **Do not sum
both metric families together.** Failed attempts without reported usage cannot be counted; this dashboard
is not a billing ledger. The app still prices failover calls using the requested model's `pricing_key`.

GPT-5.6 pricing uses Microsoft's published Standard Global **short-context** rates:
Sol $4.00 / $0.50 / $20.00, Terra $2.00 / $0.20 / $12.00, Luna $0.20 / $0.02 / $1.20
per 1M input / cached input / output tokens. Sol's input/output rates are promotional through at least
2026-11-30; recheck before later events. [Pricing source](https://azure.microsoft.com/en-us/blog/gpt-5-6-now-available-in-microsoft-foundry/).
GPT-5.5 judge pricing is $5.00 / $0.50 / $30.00 per 1M input / cached input / output tokens
([Microsoft pricing source](https://azure.microsoft.com/en-us/blog/openais-gpt-5-5-in-microsoft-foundry-frontier-intelligence-on-an-enterprise-ready-platform/));
measure its unscored cost and latency during the dry run.

The GPT-4.1 family is deprecated for new customers in 2026; if your subscription still has access you can switch back
with the commented block in `infra/terraform.tfvars.example` (`pricing.json` keeps the `gpt-4.1*` prices).
Reasoning tokens are billed as **output** tokens and count against `max_completion_tokens` — keep `reasoning_effort` at
`none`/`low` for cheap tiers (per-model `extra_body` in `terraform.tfvars`).

Any key in `models.json` works with `compare`, `default_model` and the router's tier mapping; keys outside the
`economy → balanced → premium` chain (`open`, `selfhosted`, `fw`, `fw_*`, `custom`) escalate straight to `premium`.

### Optional: Fireworks Arena (Challenge 2.4) and Multi-Provider Failover (Challenge 3.6)

Fireworks on Foundry lets you compare the current **open premium** `FW-GLM-5.3-Flash` model with the closed premium
models. One Fireworks deployment is enough for the Arena and for failover; deploying separate fast/pro models added
cost and setup time without enabling a distinct workshop capability.

```bash
cd infra
./scripts/check-fireworks-prereqs.sh --register      # PowerShell: ./scripts/check-fireworks-prereqs.ps1 -Register
./scripts/check-fireworks-prereqs.sh                 # repeat until it reports no blockers (feature up to 30 min)
terraform apply -var 'deploy_fireworks=true'         # or set deploy_fireworks = true in tfvars; a deployment can take up to 30 min
# Challenge 3.6 (stretch: failover only; provider routing already works):
terraform apply -var 'deploy_fireworks=true' -var 'apim_policy_file=policies/ai-gateway-multiprovider.xml'
```

Do this **during setup** (ideally the day before), not during the challenge. Current limitations: pay-per-token Data
Zone Standard is **US-only** (eastus, eastus2, centralus, northcentralus, westus, westus3) and Fireworks on Foundry is
currently **excluded from the EU Data Boundary** (the workshop uses synthetic data); chat completions only; adaptive
rate limits can return 429s; per-token models can retire with 15 days' notice. Fireworks prices in `pricing.json` are
marked `"verified": false` until the coach confirms them. With the 3.6 policy, a 429/5xx from the Azure backend for a
mapped model is retried once on Fireworks; the response header `x-tokenwars-provider` shows who answered.

## Rules in one paragraph

Score = **total cost ÷ successful answers** (judge score ≥ 4 of 5) over the full 100-question workload. A run is
only valid with a pass rate ≥ 85 %. The answering pipeline may only use `id`, `customer_id` and `question` from the
workload — using `category`, `difficulty`, `reference_answer` or `must_include` means disqualification. Judge costs
are not counted. Details: [`coach/scoring.md`](coach/scoring.md).

## Cleanup

```bash
cd infra
terraform destroy
```
This deletes the resource group with all Foundry resources (including the optional Fireworks resource), model
deployments, APIM, Container Apps and logs (soft-deleted Foundry resources and APIM instances are purged). Nothing keeps billing afterwards. The generated `.env` and `models.json` stay on disk —
delete them if you like.

## Disclaimer

ByteCart, its customers, orders and policies are fictional. Prices in `shared/config/pricing.json` are illustrative
list prices — update them to current Azure pricing for your region before the event. Entries with `"verified": false`
(the Fireworks models) could not be confirmed on the official pricing page.
