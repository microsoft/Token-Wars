# Own the Weights — coach demo kit (coach subscription only)

> **Coach-only.** Teams do **not** run this. It uses the coach's own Azure subscription, takes hours of wall-clock
> time and has an hourly hosting fee. Everything here is **pay-per-token + hourly hosting**. No PTU is used anywhere.

**Story for the room:** *"What if ByteCart owned a small specialist model instead of renting the premium model?"*
You distil the premium model into a small open-weight model (**Ministral-3B (2411)** by default) with Microsoft Foundry
**serverless supervised fine-tuning (SFT)**. Then you compare it live against `balanced` and `premium` with the workshop's own
`compare` command, and you discuss **when the hourly hosting fee pays off** with `breakeven.py`.

Facts below were checked on Microsoft Learn and the Azure pricing page on **2026-10-09**. Items marked **VALIDATE** could
not be confirmed. Re-check them one week before the event.

---

## 1. Why this is coach-only

| Reason | Detail |
|---|---|
| Wall-clock time | Queueing + training + deployment can take hours (VALIDATE: typically 1–3 h for ~400 short examples). That doesn't fit in a 4-hour workshop. |
| Running cost | A deployed fine-tuned model bills an **hourly hosting fee whether or not you call it**. A team that forgets to delete it pays for days. |
| Permissions | Training needs **Foundry User**. Deploying needs **Foundry Owner** (or `Microsoft.CognitiveServices/accounts/deployments/write`). Many team subscriptions don't grant this. |
| Region / quota | Global training only runs from supported project regions (Sweden Central is supported). The deployment needs Global Standard fine-tuned quota. |
| Fireworks custom weights | Fireworks on Foundry serves imported or fine-tuned **custom** weights **only with provisioned throughput (PTU)**. This kit therefore covers Fireworks only as a **portal walkthrough** (section 9). Nothing is deployed. |

## 2. What the platform supports (summary, checked 2026-10-09)

Source: *Fine-tuning in Microsoft Foundry* (overview), the supported-models table and its legends.

| Base model | Managed method | Training types | Serverless deployment |
|---|---|---|---|
| **Ministral-3B (2411)** (default) | SFT | Global, Data zone (US) | **Global Standard only** |
| gpt-oss-20b | SFT | Global, Data zone (US) | Global Standard only |
| Qwen3-32B | SFT | Global, Data zone (US) | Global Standard only |
| Llama-3.3-70B-Instruct | SFT | Global, Data zone (US) | Global Standard only |

* **Global training** is cheaper than Standard and uses capacity outside your region. Data and weights are copied to the
  training region, so there is **no data residency**. Sweden Central is in the *Supported global training regions* table.
* The REST job body selects the training type with `"trainingType": "GlobalStandard"` (the Python SDK passes it in
  `extra_body`). Other values: `Standard`, `Developer`.
* Data format: chat JSONL `{"messages": [...]}`. Files must be **UTF-8 with a BOM** (as documented) and under 512 MB.
  You need at least 10 training examples. Hundreds are recommended.
* **Billing:** SFT training is charged per training token × epochs. A **Global Standard** fine-tuned deployment charges
  per-token inference **plus a fine-tuned-model hourly hosting fee**. The no-hosting-fee **Developer** deployment type
  isn't available for these open-weight models (legend: Global Standard only).
* Deployments of customised models that get **no calls for 15 days are deleted automatically**. The model itself is kept.
  Don't rely on this to save money: you pay hosting for those 15 days.
* For Azure OpenAI fine-tunes the docs give **$1.70/h** as the hosting example. For open-weight fine-tunes, use the pricing
  page values below.

**Fine-tuning prices, Global (Azure pricing page "Foundry Models – Fine-tuning models", USD, 2026-10-09):**

| Model | Training / 1M tokens | Hosting / hour | Input / 1M | Output / 1M |
|---|---|---|---|---|
| Ministral 3B | $1.00 | $0.65 | $0.05 | $0.15 |
| Qwen3 32B | $3.20 | $0.30 | $0.30 | $1.20 |
| Llama 3.3 70B | $4.50 | $0.30 | $0.71 | $0.71 |
| gpt-oss-20b | **VALIDATE** (not listed) | **VALIDATE** | **VALIDATE** | **VALIDATE** |

Data Zone (US) prices are about 10 % higher. `breakeven.py` has these as presets (`--ft-base`). The gpt-oss-20b preset is illustrative.

## 3. Files

| File | Purpose |
|---|---|
| `generate_dataset.py` | Builds `train.jsonl` / `validation.jsonl` (90/10) **from the knowledge-base sections only**. The teacher is the `premium` model in `models.json`; `--mock` runs offline and deterministic. Drops duplicates and anything with **token Jaccard ≥ 0.6** to a `workload.jsonl` question (evaluation leakage). Writes `dropped.jsonl` and `manifest.json`. |
| `finetune.sh` / `finetune.ps1` | `preflight → upload → create → wait → deploy → test`, plus `status`, `delete` and `cleanup-files`. Uses the OpenAI v1 data plane (`/files`, `/fine_tuning/jobs`) and an ARM PUT for the Global Standard deployment. Both scripts share the state file `data/.finetune-state`. |
| `register_custom_model.py` | Adds or updates the `custom` key in `shared/config/models.json` (with `hourly_cost_usd`). With `--write-pricing` it also adds `custom-finetuned` to your **local** `pricing.json`. `--remove` cleans up. |
| `breakeven.py` | Monthly cost table: premium vs balanced vs the fine-tuned model (per-token + hosting × 730 h) vs optional Fireworks per-token. Prints the break-even volume. PTU appears only as a reference line. |
| `kit_common.py` | Shared stdlib helpers: compact prompt, keyword retrieval and `.env` parsing, copied from the SPEC so the kit never imports the app. |

Requirements: Python 3.10+, `requests` (real teacher mode only), Azure CLI, and `curl` (bash) or PowerShell 7+.
Generated data goes to `coach/own-the-weights/data/`, which is git-ignored.
The Bash script streams large Foundry JSON responses to Python through standard input, so `/models` responses are not
subject to the operating system's command-line argument-size limit.

**Required inference gateway:** the real teacher and registered `custom` model use the coach's APIM gateway
and subscription key (`via_gateway: true`). Deploy the fine-tuned model on the primary Foundry resource so
the built-in Azure route can serve it. For another resource/provider, configure its APIM backend, routing
rule and credentials first; changing only `base_url` is not sufficient. Only the independent judge is direct.
Affinity headers are supported, but inference `extra_headers` must not override gateway credentials.
`finetune test` also uses APIM and loads its credentials from the generated registry and `.env`.
Fine-tuning management operations (`/files`, `/fine_tuning/jobs`, ARM deployment) remain direct;
they are not inference calls. The scripts' `test` command needs Python and gateway configuration,
but does not acquire an Azure management token.

### Training example format (same layout the apps send: compact + cache-friendly, SPEC 4.3)

```json
{"messages": [
  {"role": "system", "content": "You are ByteCart's customer support assistant. Answer using ONLY the context provided.\n…\nAnswer in English. Do not invent policies."},
  {"role": "user", "content": "Customer ID: C1016\nToday: 2026-09-15\n\nContext:\n<top-3 keyword-retrieved KB sections>\n\nQuestion: …"},
  {"role": "assistant", "content": "<concise teacher answer>"}]}
```

The system prompt ends with the TODO 2.3 adaptation line because the apps add it for the `custom` key.
`compare` runs without escalation, so the `ESCALATE` instruction isn't in the training data. Use `--no-adaptation` if your
app version doesn't add the line.

## 4. Cost estimate for one demo (Ministral-3B, Sweden Central project, Global training)

| Item | Estimate |
|---|---|
| Teacher generation (68 question calls + ~400 answers on gpt-5.6-sol, ~1k in / 120 out) | ≈ $3 |
| Training (~250k tokens × 3 epochs × $1/1M) | ≈ $0.75 |
| Hosting $0.65/h × ~26 h (deploy T-1 afternoon → delete after the event) | ≈ $17 |
| Inference during `compare` (30 items) | < $0.05 |
| **Total** | **≈ $21** (Qwen3-32B: training ≈ $2.40, hosting $0.30/h) |

The biggest risk is a deployment you forget to delete: $0.65 × 730 h ≈ **$475/month**.

## 5. Timeline

| When | What |
|---|---|
| **T-7 days** | Confirm the coach subscription, region (`swedencentral`), roles (Foundry Owner), and fine-tuned Global Standard quota. Re-check section 2 and the VALIDATE list. Deploy the Token Wars infra in the coach subscription (`team_name = "coach"`), which gives you `.env` and `models.json`. |
| **T-2 days** | Run `generate_dataset.py --mock` (plumbing check), then the real teacher run. Review `manifest.json`, spot-check ~10 examples and read the `leakage` rows in `dropped.jsonl`. |
| **T-1 day** | `finetune all` (train + deploy). Run `register_custom_model.py --write-pricing`, then `doctor` and a dry-run `compare --models balanced,premium,custom`. Save the compare JSON as a backup slide. |
| **Event** (≈10 min in "Final Showdown & Debrief", or after Challenge 2) | Live `compare --models balanced,premium,custom`, then `breakeven.py` at 100k and 1M requests/month. Use the debrief talking points (section 11). |
| **After the event** | `finetune delete` (stops the hosting fee), `register_custom_model.py --remove --write-pricing`, `git checkout shared/config/pricing.json`, optionally `finetune cleanup-files`. |

## 6. Step by step

Prerequisite: the coach subscription has the Token Wars infra deployed, so `ROOT/.env` (`APIM_SUBSCRIPTION_KEY`) and
`shared/config/models.json` (with `premium`) exist. Run everything from `coach/own-the-weights/`.

### 6.1 Generate the dataset (T-2)

```bash
# bash
cd coach/own-the-weights
python3 generate_dataset.py --mock --out ./data-mock          # offline plumbing check, no network
python3 generate_dataset.py --n 400 --seed 42 --out ./data    # real: teacher = models.json "premium"
cat data/manifest.json
```
```powershell
# PowerShell
cd coach/own-the-weights
python generate_dataset.py --mock --out ./data-mock
python generate_dataset.py --n 400 --seed 42 --out ./data
Get-Content data/manifest.json
```

Useful options: `--top-k 3` (retrieved sections per example), `--leak-threshold 0.6`, `--out-of-scope 0.04` (share of
"I'll connect you with a human agent" examples), `--no-bom`, `--root <ROOT>` / `TOKENWARS_ROOT`.
Delete `data-mock/` afterwards.

### 6.2 Train and deploy (T-1)

```bash
# bash
export AZ_SUBSCRIPTION_ID=<coach-sub> AZ_RESOURCE_GROUP=rg-tokenwars-coach FOUNDRY_ACCOUNT=<foundry-account-name>
# optional (no commas between Bash assignments):
export BASE_MODEL=gpt-oss-20b N_EPOCHS=3
# export FOUNDRY_API_KEY=... # otherwise an Entra ID token is used
az login
./finetune.sh preflight      # lists base-model ids that look fine-tunable – set BASE_MODEL to the exact id
./finetune.sh all            # upload → create → wait (polls every 60 s) → deploy → test
```
```powershell
# PowerShell 7+
$env:AZ_SUBSCRIPTION_ID='<coach-sub>'; $env:AZ_RESOURCE_GROUP='rg-tokenwars-coach'; $env:FOUNDRY_ACCOUNT='<foundry-account-name>'
az login
./finetune.ps1 preflight
./finetune.ps1 all
```

You can run the steps one at a time (`upload`, `create`, `wait`, `deploy`, `test`). `wait` can be interrupted and resumed,
because job and file ids live in `data/.finetune-state`. If `deploy` fails on the model format, deploy once in the portal
(*Fine-tuning → job → Deploy → Serverless → Global Standard*) and keep going with step 6.3.

If the create payload shows a model such as `"gpt-oss-20b,"`, the comma became part of `BASE_MODEL`. Reset it with
`export BASE_MODEL=gpt-oss-20b`. Both scripts reject commas, spaces and quotes in model identifiers before making calls.

### 6.3 Register as `custom` and dry-run

```bash
python3 register_custom_model.py --deployment bytecart-ft --resource "$FOUNDRY_ACCOUNT" --hourly-cost 0.65 --write-pricing
cd ../../python/solution && python -m tokenwars doctor && python -m tokenwars compare --models balanced,premium,custom
# .NET: cd dotnet/solution/TokenWars && dotnet run -- compare --models balanced,premium,custom
```
```powershell
python register_custom_model.py --deployment bytecart-ft --resource $env:FOUNDRY_ACCOUNT --hourly-cost 0.65 --write-pricing
cd ../../python/solution; python -m tokenwars doctor; python -m tokenwars compare --models balanced,premium,custom
```

`--write-pricing` edits the tracked `pricing.json` in **your checkout only**. Don't commit it. Without it, the apps count
`custom` calls as $0. `compare` shows the extra *hosting $/h* column because `custom.hourly_cost_usd > 0` (AMENDMENT B3).

### 6.4 Break-even

```bash
python3 breakeven.py                               # defaults: 1M requests/month, 1,200 in / 120 out, FT prompt 800 in
python3 breakeven.py --requests 100000             # low volume → hosting fee dominates
python3 breakeven.py --ft-base qwen3-32b --cached-share 0.5
python3 breakeven.py --input-tokens 1350 --output-tokens 95 --ft-input-tokens 700   # plug in numbers from compare JSON
```

The input-token default of 800 for the fine-tuned model reflects a "short prompt": the fine-tune has learned the answer
style, so you can drop format instructions and examples and keep only the retrieved context. Put your measured averages
from `results/compare-*.json` into the flags.

### 6.5 Clean up (right after the event)

```bash
./finetune.sh delete && python3 register_custom_model.py --remove --write-pricing && git checkout ../../shared/config/pricing.json
./finetune.sh status        # must say: deployment not found (no hosting fee)
```
```powershell
./finetune.ps1 delete; python register_custom_model.py --remove --write-pricing; git checkout ../../shared/config/pricing.json
./finetune.ps1 status
```

## 7. Sample output

`python3 breakeven.py --premium-key gpt-5.6-sol --balanced-key gpt-5.6-terra`
(repo `pricing.json` on 2026-10-09; explicit keys also work before Terraform updates an existing deployment):

```text
Token Wars – Own the Weights break-even · 1,000,000 requests/month · 1,200 in / 120 out tokens (fine-tuned: 800 / 120)
Prices: shared/config/pricing.json; fine-tuned: preset ministral-3b. List prices, USD – illustrative, check before the event.

Option                                                $/1M in  $/1M out  tokens $/mo  hosting $/mo  TOTAL $/mo  $/1k req
------------------------------------------------------------------------------------------------------------------------
premium + retrieval (gpt-5.6-sol)                      4.000    20.000       $7,200             -      $7,200    7.2000
balanced + retrieval (gpt-5.6-terra)                        2.000    12.000       $3,840             -      $3,840    3.8400
Ministral-3B (2411) FT, short prompt                    0.050     0.150       $58.00          $474        $532    0.0580
Fireworks per-token (fw-glm-5.3-flash)                  0.150     0.500         $240             -        $240    0.2400 ◀ cheapest
------------------------------------------------------------------------------------------------------------------------
⚠️  Fireworks per-token (fw-glm-5.3-flash): price not verified (pricing.json verified=false).
Break-even vs balanced: 125,463 requests/month (≈ 4,182/day). Above that the fine-tuned model wins; below it the hosting fee dominates.
  vs premium + retrieval (gpt-5.6-sol): 66,438 requests/month
  vs Fireworks per-token (fw-glm-5.3-flash): 2,604,396 requests/month
One-off training: 250,000 tokens × 3 epochs × $1.0/1M ≈ $0.75 (plus teacher-generation tokens).
Hosting is billed per hour while the deployment EXISTS, even with zero traffic – delete it after the demo.
PTU (for reference, not recommended for this workshop): 15 PTU × $1.0/PTU-h × 730 h ≈ $10,950/month regardless of volume (illustrative rate – VALIDATE).
```

At `--requests 100000` (with the same GPT-5.6 keys) the order flips. Balanced costs $384/month and the fine-tuned model costs $480/month, which is almost
all hosting fee.

`python3 generate_dataset.py --mock --out <dir>` (deterministic):

```text
📚 KB: 68 sections · workload: 100 questions · mode: mock · target n=400
🧹 Candidates 636 → kept 629 (dropped: leakage 1 [Jaccard ≥ 0.6], exact dup 0, near dup 6, empty 0, bad answer 0)
🎯 Out-of-scope hand-off examples: 12 · retrieval misses (source section not in top-3): 24
✅ Wrote 360 train + 40 validation examples to <dir>
💶 ≈201,769 training tokens × 3 epochs ≈ $0.61 at $1.0/1M (estimate; check the pricing page)
```

## 8. Validation checklist

- [ ] VALIDATE items (section 12) re-checked one week before the event. Prices updated in `breakeven.py` presets if they changed.
- [ ] `generate_dataset.py --mock` runs offline. The real run reports **leakage > 0 or explicitly 0**, and you read the `dropped.jsonl` leakage rows.
- [ ] Spot-checked 10 random training examples: correct facts, no order IDs, concise, no `ESCALATE`.
- [ ] `manifest.json`: train ≥ 300, validation ≥ 30, retrieval misses understood.
- [ ] `finetune preflight` shows the right account, region `swedencentral`, and the exact `BASE_MODEL` id.
- [ ] Job `succeeded`. Validation loss fell and didn't diverge from training loss (portal metrics / `results.csv`).
- [ ] Deployment SKU is **GlobalStandard** (`finetune status`). No PTU anywhere.
- [ ] `finetune test` returns a sensible answer.
- [ ] `register_custom_model.py --write-pricing` done. `doctor` shows `custom` reachable.
- [ ] Dry-run `compare --models balanced,premium,custom` finished. Results JSON saved as a backup slide.
- [ ] Calendar reminder: **delete the deployment** right after the session. `finetune status` confirms it.
- [ ] `pricing.json` reverted (`git status` clean), `custom` removed from `models.json`.

## 9. Fireworks on Foundry: portal walkthrough only (no deployment)

Show this in the Foundry portal (≈2 min) to close the loop with Challenge 2.4 "Fireworks Arena":

1. Foundry portal → your project → *Fine-tuning* → completed job → **Deploy** → options *Serverless*, *Managed compute (preview)*, *Fireworks (preview)*.
2. Point out that Fireworks serves **one adapter merged into a full-weight copy of the base model**, and that **custom models on
   Fireworks use provisioned throughput (PTU-hours)**. Pay-per-token Fireworks offers exist only for supported catalog models,
   which is what teams used in the Arena.
3. Point out the Fireworks prerequisites: a Subscription Owner/Contributor enables Fireworks on Foundry, quota is needed, and the models table
   lists Fireworks deployment only for some preview bases (e.g. Qwen3.6-35B-A3B, gpt-oss-120b), all **PTU-only**.
4. Close the walkthrough without deploying. The workshop rule is pay-per-token only, and PTU is out of scope.

## 10. Limitations

* **Knowledge is frozen at training time.** The training data comes from the KB as of T-2. If a policy changes, you have to retrain.
  That's why the examples keep the retrieved context in the prompt.
* KB-only data: the model never saw order JSON in training. `order_status` questions rely on its general ability to read context.
* A 3B model is weaker at multi-rule reasoning (`policy_reasoning`). Expect a lower pass rate there than `premium`.
* The teacher's mistakes get distilled too. The mock mode only exercises the plumbing; its answers are extractive.
* Leakage filtering is lexical (token Jaccard ≥ 0.6). It catches close paraphrases, not semantic ones.
  `compare` items are a **test set**: never train on them.
* Hosting is billed per hour while the deployment exists. Quotas, model ids and regions change, so see the VALIDATE list.
* Fine-tuned inference goes through required APIM (`via_gateway: true`), including the teacher and smoke probe.
  Use the primary Foundry resource or configure an explicit gateway backend/model route for another resource.

## 11. Debrief talking points

1. **Fine-tuning doesn't replace retrieval for changing facts.** It teaches *behaviour* (tone, format, brevity, reading the
   context). Facts that change (return windows, fees) stay in retrieval. Microsoft's own guidance: fine-tuning *"doesn't replace
   retrieval for current information or application-level safety controls"*.
2. **Hosting fee vs volume.** Per request, the fine-tuned 3B model is about 25× cheaper than balanced. But $0.65/h × 730 h = $474/month
   is a fixed fee that runs even at zero traffic. Break-even vs balanced ≈ **343k requests/month** at the defaults. Below that,
   per-token models (balanced, economy, Fireworks) win. Ask the room: *"How many requests does your copilot really do per month?"*
3. **Evaluation leakage.** We generated the training questions from the KB and dropped anything ≥ 0.6 Jaccard to the
   workload questions. If you train on your test set, your leaderboard numbers stop meaning anything. Keep a held-out set, and
   remember the judge's reference answers come from the same KB.
4. **Total cost of ownership:** teacher tokens + training + evaluation + hosting + retraining when the KB or the base model changes.
   The docs: budget for repeated runs, grading, evaluation, hosting and inference.
5. **Compare against the cheaper alternatives first.** A shorter prompt, retrieval, caching and routing (Challenges 1–3) often
   capture most of the savings without owning weights.
6. **PTU is a different conversation.** It's fixed capacity for predictable, high, steady load. It's not the default lever, and
   it's not used in this workshop.

## 12. VALIDATE list (could not be confirmed on 2026-10-09)

* **Base model id** for the REST job (`BASE_MODEL=Ministral-3B`). Run `finetune preflight` or check the portal's fine-tune
  wizard (alternatives: `gpt-oss-20b`, `Qwen3-32B`).
* **Data-plane endpoint** `https://<account>.services.ai.azure.com/openai/v1` for `/files` and `/fine_tuning/jobs` on the new
  Foundry. `https://<account>.openai.azure.com/openai/v1` is the documented fallback (`FOUNDRY_ENDPOINT`).
* **Deployment model `format`** for open-weight fine-tunes in the ARM PUT. The script looks it up with
  `az cognitiveservices account list-models` and falls back to `OpenAI`, as documented for Azure OpenAI fine-tunes. Also verify `version: "1"`.
* **`DEPLOY_CAPACITY`** (default 50) unit and the available Global Standard fine-tuned quota.
* File processing status values (`processed`/`error`) and the `capabilities.fine_tune` field used by `preflight`.
* Whether the **UTF-8 BOM** is still required (documented) or tolerated. Use `--no-bom` if upload rejects it.
* **gpt-oss-20b** fine-tuning prices (not on the pricing page) and the hosting-fee values, which can change.
* Typical job duration for ~400 short examples (assumed 1–3 h including queue).
* PTU reference numbers in `breakeven.py` (`--ptu-units 15`, `--ptu-hourly 1.00`) are illustrative.
* Fireworks prices in `pricing.json` are marked `verified: false` by the infra owner.

## 13. Sources (checked 2026-10-09)

* Fine-tuning in Microsoft Foundry (overview, supported models, training/deployment types, global training regions):
  https://learn.microsoft.com/azure/foundry/fine-tuning/overview
* Deploy fine-tuned models in Microsoft Foundry: https://learn.microsoft.com/azure/foundry/fine-tuning/deploy-fine-tuned-models
* Manage fine-tuning costs: https://learn.microsoft.com/azure/foundry/fine-tuning/cost-management and
  https://learn.microsoft.com/azure/foundry/openai/how-to/fine-tuning-cost-management ($1.70/h example)
* SFT how-to (REST/SDK, `trainingType`, ARM deployment PUT `api-version=2024-10-01`):
  https://learn.microsoft.com/azure/foundry/openai/how-to/fine-tuning and
  https://learn.microsoft.com/azure/foundry/openai/how-to/fine-tuning-deploy (15-day inactivity deletion)
* Pricing: https://azure.microsoft.com/pricing/details/ai-foundry-models/fine-tuning-models/
