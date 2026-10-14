# Coach Guide — Token Wars: Build, Route & Optimize Your AI Stack

Audience: developers/architects of digital-native companies. Format: 4 hours, teams of 2–4, **each team brings its own Azure
subscription**, Python **or** .NET. The coach uses a **separate** subscription for the leaderboard and for demos teams
cannot do in 4 hours ("Own the Weights"). Goal: lowest **cost per successful answer** with pass rate ≥ 85 %.
Principle: **pay-as-you-go first** — every model is billed per token; nothing in the kit requires PTU.

Related: [pre-event-checklist.md](pre-event-checklist.md) · [scoring.md](scoring.md) · [troubleshooting.md](troubleshooting.md) ·
[own-the-weights/README.md](own-the-weights/README.md) · participant instructions `docs/index.html`.

---

## 1. Before the event (T-14 days → T-1 hour)

Full per-team and coach checklist: [pre-event-checklist.md](pre-event-checklist.md). Short version:

| When | What |
|---|---|
| T-14 d | Send every team [pre-event-checklist.md](pre-event-checklist.md): own subscription, roles, quota, tool versions, optional Fireworks registration. |
| T-7 d | Confirm every team has its **own subscription** and **model quota** in the chosen region (default `swedencentral`): gpt-5.6-sol / gpt-5.6-terra / gpt-5.6-luna for answers, gpt-5.5 (2026-04-24) for the independent judge, text-embedding-3-small, Llama-3.3-70B-Instruct (GlobalStandard). Defaults request 800K TPM for each of premium, balanced, economy and judge, 150K (embedding), 100K (Llama) per team — if a team has less quota, lower `capacity`. Terra and GPT-5.5 each need their own 800K TPM allocation. Optional secondary chat deployments request 800K TPM each in their region. GPT-4.1 is *deprecated* for new customers in 2026 (commented fallback in `terraform.tfvars.example`). |
| T-7 d | Check that Azure Policy does not force `disableLocalAuth` on Foundry resources (the apps use API keys), that allowed regions include `swedencentral` (and a US region if the team wants the Fireworks Arena) and that users can create role assignments (APIM managed identity). |
| T-7 d | **Dry run in a fresh subscription** (section 11): `terraform init -upgrade` + `apply` (azurerm 5.x), `doctor`, baseline, solution, `compare`, APIM solution policy, Fireworks Arena and the 3.6 failover policy. Work through the VALIDATE list. |
| T-7 d | **Confirm the Fireworks per-token model list and prices** (offers can retire with 15 days' notice): `infra/scripts/check-fireworks-prereqs.sh` output, Foundry model catalog, Azure pricing calculator. Update `fireworks_models` / `shared/config/pricing.json`; set `"verified": true` only for confirmed prices. Update the other prices too. |
| T-7 d | "Own the Weights": confirm the coach subscription, roles (Foundry Owner) and fine-tuned Global Standard quota ([own-the-weights/README.md](own-the-weights/README.md) section 5). |
| T-2 d | "Own the Weights": generate and review the dataset. |
| T-1 d | Deploy the leaderboard (section 8), note URL + submit key, submit one real test run, then reset the board. |
| T-1 d | Teams run `terraform apply` (incl. `deploy_fireworks = true` if they want the Arena) and `doctor` the day before — APIM, quota and Fireworks opt-in/deployment times (up to 30 + 30 min) are the #1 time sink. |
| T-1 d | "Own the Weights": train + deploy, `register_custom_model.py --write-pricing`, dry-run `compare --models balanced,premium,custom`, save the result as a backup slide. |
| T-1 h | Leaderboard on the big screen, slides ready, `coach/` docs open, solution folders known, `custom` model answers in `doctor`. |

Hand out per team: team name, `leaderboard_url`, `leaderboard_key` (go into `terraform.tfvars`). The subscription is
the team's own.

---

## 2. Run of show

| Time | Block | Coach does | Teams do |
|---|---|---|---|
| **0:00–0:30** | **Kickoff — "Enter the Arena"** | Story (ByteCart, expensive copilot), metric, rules, agenda, leaderboard. Live demo of `run --limit 20` scorecard. | `az login`, `terraform apply` (if not done), `doctor`, first baseline `run --limit 20`, then full baseline `run --submit`. |
| **0:30–1:30** | **Challenge 1 — "The Token Diet"** | Walk the room, unblock infra, push teams to measure after each change. 0:55 mini-checkpoint: "who has keyword retrieval working?" | TODO 1.1–1.7: compact prompt, keyword retrieval, order lookup, output control, exact + semantic cache, cache-friendly layout. Submit. |
| **1:30–1:40** | **Break** | Read leaderboard aloud, praise biggest improvement. Option B for "Own the Weights": start the `compare --models balanced,premium,custom` run on the big screen (section 5). | — |
| **1:40–2:40** | **Challenge 2 — "Bring Your Own Model"** | 5-min intro to model tiers, open-weight vs self-hosted TCO, open vs closed premium (2.4). | TODO 2.1–2.3: open/self-hosted model, `compare`, pick model per tier, prompt adaptation. Optional 2.4 *Fireworks Arena* (split below). Submit. |
| **2:40–3:30** | **Challenge 3 — "Route & Rule"** | 5-min intro to routing + AI gateway. Demo the APIM token limit (429) with two consumers. | TODO 3.1–3.5: rules/classifier router, escalation, retry, APIM policy, `use_gateway`. Teams done by ~3:05 with APIM + Fireworks: stretch 3.6 *Multi-Provider Failover*. Final submissions by **3:25**. |
| **3:30–4:00** | **Final Showdown & Debrief** | Freeze board, top-3 teams explain their strategy (3 min each), "Own the Weights" demo (~8 min, section 5), debrief (section 9), cleanup reminder. | `terraform destroy` (unless they want to keep exploring). |

Timeboxing tips: if a team is > 20 min behind at 0:50, tell them to copy the solution for 1.2/1.3 and move on —
the fun is in the trade-offs, not in typing tokenisers.

**Challenge 2 — suggested split of the 60 minutes** (2.4 only for teams that deployed Fireworks during setup):

| Time | Step |
|---|---|
| 1:40–1:45 | Coach intro: model tiers, open-weight vs self-hosted TCO, open vs closed premium |
| 1:45–1:55 | 2.1 open / self-hosted model, `doctor` |
| 1:55–2:10 | 2.2 `compare --models premium,balanced,economy,open` (teams with Fireworks add `fw` here and save a `compare` run), pick a model per tier |
| 2:10–2:20 | 2.3 prompt adaptation (also applies to `fw` / `fw_*`) |
| 2:20–2:35 | 2.4 *Fireworks Arena*: decide which tier `fw` replaces by cost per success + p95, edit `TIER_MODELS` / `Router.TierModels` or `default_model`, quick run. Teams without Fireworks: tune the tier mapping instead. |
| 2:35–2:40 | Full run + submit |

Never let a team start the Fireworks opt-in or deployment during Challenge 2: up to 30 min for the feature plus up to
30 min per deployment. If it is not ready by 1:40, they skip 2.4.

**Own the Weights demo — where to put it** (~8–10 min, coach subscription, details in section 5):
- **Option A (recommended): 3:30 debrief**, right after the top-3 presentations — it closes the "what else could we
  do?" question: 3:30–3:40 top-3, 3:40–3:48 Own the Weights, 3:48–3:58 debrief, 3:58 cleanup reminder.
- **Option B: the 1:30 break** — run `compare --models balanced,premium,custom` live on the big screen while teams take a
  break, then show only `breakeven.py` and the talking points in the debrief (~4 min).

---

## 3. Challenge talking points

### Kickoff — "Enter the Arena"
- The metric is **cost per *successful* answer**, not cost per call: a cheap wrong answer is infinitely expensive.
- Show `shared/prompts/system-baseline.md`: two dynamic lines on top (`{{now}}`, `{{customer_id}}`) — every call
  has a different prefix, so **Azure OpenAI prompt caching (automatic, ≥ 1,024 identical prefix tokens) never hits**.
- The baseline stuffs ~7.8k tokens of KB + ~7k tokens of orders JSON (all customers — also a privacy smell!) into
  every call → ~15k input tokens × 100 questions.
- Quality bar: LLM-as-a-judge (`judge` deployment, GPT-5.5 version 2026-04-24, capacity 800) with reference answers;
  judge cost is excluded. It calls Azure directly, bypasses APIM and is distinct from the GPT-5.6 answer tiers.
- GPT-5.5 was chosen for the short reference-answer/JSON grading flow without changing the challenge pipeline.
  GPT-6.1 Sol can be evaluated for a future event; newer is not proof of better grading on this workload.
  Model separation does not remove all bias: calibrate a sample against human grades before the event.
- Pin the same judge version, prompt, reasoning effort (`none`) and settings across teams. Challenges 1–3,
  the rubric and ≥85% bar are unchanged. Repeat baselines/comparisons after the evaluator migration; do not mix
  former Terra-judge scores with GPT-5.5 scores in the leaderboard or improvement calculations.

### Challenge 1 — "The Token Diet" (TODO 1.1–1.7)
- **1.1 Compact prompt**: instructions from ~800 to ~60 tokens; "at most 5 short sentences" also cuts output tokens.
- **1.2 Keyword retrieval**: top-3 `##` sections (~500–700 tokens) instead of the whole KB. Classic BM25-ish idf; no
  vector DB needed. Discuss recall risk: policy questions spanning 2 docs need `top_k` 3–4.
- **1.3 Order lookup**: only the order(s) in the question or the customer's 3 most recent orders. Mention data
  minimisation (GDPR) as a free side benefit.
- **1.4 Output control**: `max_output_tokens` (350, sent as `max_completion_tokens` for GPT-5.6) + concise style. Output tokens are **5× the input price** on gpt-5.6-sol at the current promotion ($20 vs $4 per 1M) — the verbose baseline answers hurt.
- **Reasoning tokens** (teaching point): GPT-5.6 models can "think" before answering. Reasoning tokens are invisible in the answer but **billed as output tokens** (`usage.completion_tokens_details.reasoning_tokens`) and they **count against `max_completion_tokens`** — with a low cap and higher effort the model can spend the whole budget thinking and return an **empty answer**. Terraform sets `reasoning_effort: "none"` via `extra_body`; raising it to `low` only for the premium/COMPLEX tier is a legitimate quality-vs-cost lever. Temperature is not supported by these models (`supports_temperature: false` → the apps omit it).
- **1.5 Exact cache**: ~9 of the 25 FAQ repeats are literal (after normalisation). Key must include `customer_id` for
  order-specific questions — the workload contains **trap pairs** ("When will my last order arrive?" asked by two
  different customers).
- **1.6 Semantic cache**: embeddings + cosine ≥ 0.92, **never** for order-specific questions. Discuss threshold
  tuning: too low → wrong answers (quality drops), too high → no hits.
- **1.7 Cache-friendly layout**: static system prompt first, dynamic data in the user message → the provider's
  automatic prompt caching discounts cached input tokens (87.5 % cheaper on Sol at the current promotion: $0.50 vs $4; 90 % cheaper on Terra/Luna). Works best with
  `retrieval: "all"` (big static prefix); with top-k retrieval the static prefix is short, so the win is smaller —
  a nice trade-off discussion.

### Challenge 2 — "Bring Your Own Model" (TODO 2.1–2.3)
- **2.1** Open-weight model via Foundry (Llama-3.3-70B, serverless pay-per-token) and optionally a self-hosted
  `llama3.2:3b` on Container Apps (`deploy_selfhosted_model = true`, ~5 min to pull).
- **2.2** `compare` on the 30 `compare:true` items: premium vs balanced vs economy vs open. Typical insight: **balanced is the
  sweet spot** for quality; economy is great for FAQs but weaker on multi-rule reasoning. Llama-3.3-70B ($0.71 in / $0.71
  out) is cheaper than gpt-5.6-terra on input ($2) and about **17× cheaper on output** ($12) — for short answers it
  can be the cheapest capable option *if* its pass rate holds. Price per token is only half the story: compare
  cost per *success*.
- Self-hosted shows **$0 token cost** on the scorecard but: slow on CPU (p95 of tens of seconds), weaker quality,
  and a fixed infra cost (~$0.45/h for 4 vCPU/8 GiB) that the per-token metric does not show → TCO discussion.
- **2.3** Model-specific prompt adaptation ("Answer in English. Do not invent policies.") for every non-OpenAI key:
  `open`, `selfhosted`, `custom`, `fw` and `fw_*`.

### Challenge 2.4 — "Fireworks Arena" (optional)
- Fireworks on Microsoft Foundry: one current open-weight **premium** model (default `fw` =
  `FW-GLM-5.3-Flash`) served pay-per-token (Global Standard) from a second Foundry resource. Deployed during setup
  (`deploy_fireworks = true`); during the challenge it is pure measurement.
- Flow: `doctor` (shows `fw`) → `compare --models balanced,premium,fw` → decide which tier the
  Fireworks model replaces by **cost per success + p95** → edit `TIER_MODELS` (Python, `tokenwars/router.py`) /
  `Router.TierModels` (.NET) or `default_model` → full run → submit.
- Price lens (`pricing.json`, **`verified: false`** — illustrative): `fw` $0.15 in / $0.03 cached / $0.50 out
  vs economy $0.20 / $1.20, balanced $2 / $12 and premium $4 / $20. The model is inexpensive on paper, but the
  judge decides, and cross-region p95 matters too.
- `fw` / `fw_*` use APIM (`via_gateway: true`, caller key `APIM_SUBSCRIPTION_KEY`) with model-based routing in every policy.
  The Fireworks backend owns its provider credentials. Escalation from a Fireworks
  answer goes straight to `premium`.
- Prompt caching: Terraform sets `extra_body = { prompt_cache_key = "bytecart-support" }`; `user` or an
  `x-session-affinity` header (`extra_headers`) also help routing to a warm cache. Stretch: compare `cached_tokens`
  with and without `prompt_cache_key` (remove it from `extra_body` in `models.json` for one run).
- Adaptive rate limits → expect some 429s: TODO 3.4 retry pays off here. Default quota is 10M TPM per region per pool,
  shared by all Fireworks models.
- Small open models (Llama 3.1 8B, Qwen3.5 9B, gpt-oss-20b, Ministral 3B) are **PTU-only** on Fireworks, so the
  Arena is *open premium vs closed premium*, not "tiny vs big".

### Challenge 3 — "Route & Rule" (TODO 3.1–3.5)
- **3.1 Rules router**: deterministic, free, explainable; order questions → balanced, long/multi-clause → premium,
  complaints → balanced, rest → economy.
- **3.2 Classifier router**: economy with 5 max tokens costs ~$0.00003 per question; usually beats rules on hard items.
  With reasoning models the 5-token cap only works with `reasoning_effort: none` — any reasoning would eat the budget
  and return an empty label (→ falls back to `default_model`).
  Remind: routing on `category`/`difficulty` from the workload = disqualification.
- **3.3 Escalation**: cheap model answers or says `ESCALATE` → next tier. Measure escalation rate; too many
  escalations = paying twice.
- **3.4 Retry**: honour `retry-after-ms` / `retry-after`, exponential backoff + jitter. Needed as soon as APIM
  token limits (429) are on.
- **3.5 AI gateway** (`infra/policies/ai-gateway-starter.xml`): `llm-token-limit` per subscription (two consumers:
  checkout-squad, support-squad), `llm-emit-token-metric` with dimensions → App Insights *Metrics* namespace
  `tokenwars`, backend pool + circuit breaker + retry on 429 for failover (`deploy_secondary_region = true`).
  APIM is already required: all Azure, Fireworks, Ollama and embedding inference uses it; only the judge is direct.
  The starter already routes by physical model and emits `team`/`provider` token metrics. TODO 3.5b enriches
  existing metrics with API/model dimensions. Show that the gateway adds governance, not savings — the token limit may *hurt*
  throughput/latency; that is the trade-off.
- Demo idea: set `tokens_per_minute_per_consumer = 2000`, run `ask` a few times with the support-squad key → 429 +
  `Retry-After`; the checkout-squad key still works.

### Challenge 3.6 — "Multi-Provider Failover" (stretch)
- Adds **failover only**; provider routing and provider telemetry already work in every policy.
  Needs `deploy_fireworks` (APIM is mandatory):
  `terraform apply -var 'deploy_fireworks=true' -var 'apim_policy_file=policies/ai-gateway-multiprovider.xml'`
  (or set both in tfvars). A precondition blocks the apply without Fireworks.
- `infra/policies/ai-gateway-multiprovider.xml` = everything from the solution policy, plus: when the **Azure backend**
  answers **429 or ≥ 500** for a model in `failover_model_map` (default `gpt-5.6-terra` and `gpt-5.6-sol` →
  `FW-GLM-5.3-Flash`; `terraform output apim_failover_model_map`), the request is retried **once** on
  backend `fireworks`. The body is rewritten: `model` → Fireworks deployment, `reasoning_effort` removed,
  `max_completion_tokens` → `max_tokens`.
- Response headers `x-tokenwars-provider` (`azure-openai` | `fireworks`), `x-tokenwars-model`, `x-tokenwars-backend`;
  token metrics carry lowercase **`provider`** and **`team`** dimensions in all policies.
  Use outbound **Provider Tokens** for consumption by the final serving provider; native LLM metrics describe
  the requested route. Do not sum the two families together.
- Unmapped models (economy, Llama, embeddings, judge) never fail over. A 429 from `llm-token-limit` is produced by APIM
  itself before any backend call, so it does **not** fail over — that is the consumer budget working as designed.
- To provoke a backend 429 for the demo: keep `deploy_secondary_region = false`, temporarily lower the mapped
  deployment's TPM (e.g. `balanced_model = { capacity = 1 }`), raise `tokens_per_minute_per_consumer`, and watch the header:
  ```bash
  cd infra
  GW=$(terraform output -raw apim_gateway_url)        # ends with /openai/v1/
  KEY=$(terraform output -json apim_consumer_keys | python3 -c "import json,sys; print(json.load(sys.stdin)['support-squad'])")
  for i in 1 2 3 4 5; do curl -s -D - -o /dev/null "${GW}chat/completions" -H "api-key: $KEY" -H "content-type: application/json" \
    -d '{"model":"gpt-5.6-terra","messages":[{"role":"user","content":"How long is the return window?"}],"max_completion_tokens":200}' \
    | grep -i -E '^HTTP|x-tokenwars-provider'; done
  ```
  Restore the capacity afterwards.
- Debrief angle: during failover a **different model answers silently** → quality drift the caller never sees unless
  it reads the header; `llm-token-limit` counts Fireworks tokens against the same consumer budget. The app prices a
  failed-over answer with the requested model's pricing key (see [scoring.md](scoring.md)).
- Several details are untested on a live gateway — see the "Multi-provider failover" checks in section 11.

---

## 4. Fireworks on Foundry — known limitations (tell teams at kickoff)

| Limitation | What it means for the event |
|---|---|
| Pay-per-token (Data Zone Standard) is **US-only**: eastus, eastus2, centralus, northcentralus, westus, westus3 | `fireworks_location` defaults to `eastus2`; a `check` block warns for other regions. Expect higher latency from EU venues. |
| Fireworks on Foundry is **excluded from the EU Data Boundary** | A current limitation, not a blocker: the workshop uses synthetic data. Mention it as a real-world architecture constraint in the debrief. |
| Subscription opt-in (`Fireworks.EnableDeploy`) takes **up to 30 min** and needs Subscription Owner/Contributor | Teams run `infra/scripts/check-fireworks-prereqs.sh --register` days before the event. |
| A deployment can take **up to 30 min** | Deploy during setup (T-1), never during Challenge 2. Terraform uses a 60-min create timeout. |
| **Adaptive rate limits** → 429s | TODO 3.4 retry handles them; default quota 10M TPM per region per pool, shared by all Fireworks models. More: aka.ms/fireworks-quota. |
| **Chat completions only** (no embeddings) | Embeddings and the semantic cache stay on `text-embedding-3-small`. |
| Per-token models can **retire with 15 days' notice** | The coach confirms the model list and prices **one week before** the event and updates `fireworks_models` / `pricing.json`. |
| Small open models (Llama 3.1 8B, Qwen3.5 9B, gpt-oss-20b, Ministral 3B) are **PTU-only** | Not used; the Arena is *open premium vs closed premium*. |
| Bring-your-own-weights / custom models on Fireworks are **PTU-only** | Shown only as a portal walkthrough in the "Own the Weights" demo — never deployed. |
| Prices could not be verified on the official page (`"verified": false`) | Announce that Fireworks prices are illustrative; they are the same for every team, so the ranking stays fair. |
| Deploying needs **Foundry Owner** (formerly Azure AI Owner) on the project | Owner/Contributor usually works; on Forbidden set `fireworks_grant_deployer_role = true` and re-apply. |

---

## 5. Coach demo — "Own the Weights" (coach subscription only)

Full kit and run-of-show: [own-the-weights/README.md](own-the-weights/README.md). Teams never run this.

- **Story:** "What if ByteCart owned a small specialist instead of renting the premium model?" A **Ministral-3B**
  model distilled from `premium` with Foundry **serverless SFT** (Global training, Global Standard deployment —
  pay-per-token plus an hourly hosting fee, no PTU) answers with a **short prompt**.
- **When:** recommended in the 3:30 debrief (~8 min, after the top-3 presentations); alternatively start the live
  `compare` during the 1:30 break and show only `breakeven.py` in the debrief (section 2).
- **Live steps:** `compare --models balanced,premium,custom` (the *hosting $/h* column appears because `custom` has
  `hourly_cost_usd`), then `python3 breakeven.py --requests 100000` and `python3 breakeven.py` (1M requests/month).
  Keep the T-1 compare JSON as a backup slide. Optionally a 2-min portal walkthrough of *Deploy → Fireworks (preview)*:
  custom weights on Fireworks are PTU-only, so nothing is deployed.
- **Talking points:**
  1. Fine-tuning teaches *behaviour* (tone, brevity, reading context), not changing facts — retrieval stays.
  2. Hosting fee vs volume: per request the 3B model is ~25× cheaper than balanced, but $0.65/h × 730 h ≈ $474/month runs
     even at zero traffic. Break-even vs balanced ≈ 343k requests/month at the defaults; below that, per-token models
     (balanced, economy, Fireworks) win. Ask: "How many requests does your copilot really do per month?"
  3. Evaluation leakage: training questions were generated from the KB and filtered against the workload (Jaccard ≥ 0.6).
  4. TCO: teacher tokens + training + evaluation + hosting + retraining when the KB or base model changes.
  5. Challenges 1–3 (prompt, retrieval, caching, routing) usually capture most of the savings without owning weights.
  6. PTU is a different conversation (steady, high, predictable load) — not used in this workshop.
- **Scoring:** the `custom` model is a coach demo and is **not eligible** for the leaderboard; do not submit demo runs.
- **Cost:** ≈ $21 per demo (teacher ≈ $3, training ≈ $0.75, hosting $0.65/h × ~26 h ≈ $17). The hosting fee bills
  **only while the deployment exists** — run `finetune delete` right after the session and confirm with
  `finetune status` (a forgotten deployment costs ≈ $475/month).

---

## 6. Expected numbers (rough ranges, full 100-item run)

GPT-5.6 quality and latency have not yet been benchmarked for this workload: measure them during the dry run.
Token-only cost estimates below assume 100 calls, uncached input, reasoning disabled, 400–1,000 output tokens
per baseline call and 100–350 per compact call. They use Standard Global short-context pricing, including Sol's
$4 input / $20 output promotion through at least 2026-11-30. Cache/routing results require measurement.

| Configuration | Input tok/call | Total cost / run | Cost / success | Pass rate | p95 latency |
|---|---|---|---|---|---|
| Baseline (starter defaults, gpt-5.6-sol) | ~15,000 | $6.80–8.00 | measure | measure | measure |
| + compact prompt, keyword top-3, order lookup, max 350 tokens (gpt-5.6-sol) | ~900–1,400 | $0.56–1.26 | measure | measure | measure |
| + exact & semantic cache (gpt-5.6-sol) | same | measure | measure | measure | measure |
| Same on `balanced` only (gpt-5.6-terra) | ~900–1,400 | $0.30–0.70 | measure | measure | measure |
| Same on `open` only (Llama-3.3-70B) | ~900–1,400 | $0.06–0.11 | $0.0007–0.0014 if valid | 80–90 % | 3–10 s |
| Same on `economy` only (gpt-5.6-luna) | ~900–1,400 | $0.03–0.07 | measure | measure | measure |
| Same on `fw` only (FW-GLM-5.3-Flash, optional) | ~900–1,400 | $0.03–0.07 ¹ | measure in the dry run | measure | measure (Fireworks region) |
| Solution (classifier + escalation + caches, balanced default) | ~1,000 | measure | measure | measure | measure |

¹ Token cost only, from the unverified `pricing.json` values (`verified: false`); pass rate and latency are not known
yet — record them during the T-7 dry run. If a Fireworks model emits reasoning tokens, output cost rises.

- Record cost-per-success improvements against the new baseline. Anything claiming > 99 % with a pass
  rate ≥ 85 % deserves a look at the code (see scoring.md, disqualification).
- Judge cost (gpt-5.5): $5 input / $0.50 cached input / $30 output per 1M tokens; measure token use and latency
  in the dry run (excluded from the score). [Microsoft pricing source](https://azure.microsoft.com/en-us/blog/openais-gpt-5-5-in-microsoft-foundry-frontier-intelligence-on-an-enterprise-ready-platform/).
- If a team raises `reasoning_effort` above `none`, expect higher output token counts (and latency) — check
  `output_tokens` in the scorecard.
- Runtime: measure baseline and optimised runs at concurrency 8 and capacity 800.

---

## 7. Hints ladder (give the lowest level that unblocks)

| TODO | Level 1 — nudge | Level 2 — approach | Level 3 — show |
|---|---|---|---|
| 1.1 | "What must the prompt say, what is decoration?" | The compact prompt text is in the spec/website; keep the date/customer lines if not cache-friendly. | `solution/.../prompts` (SOLUTION 1.1) |
| 1.2 | "Do you need all 10 docs for 'How much is express?'" | Split on `## `, prefix doc title, tokenise `[a-z0-9]+`, drop stopwords/<3 chars, score Σ idf = ln(1+N/df). | `context` (SOLUTION 1.2) |
| 1.3 | "Whose orders does this question need?" | Regex `BC-\d{5}` first; else if order-specific → customer's 3 latest by `placed_at`; else none. | `context` (SOLUTION 1.3) |
| 1.4 | "What does an answer cost vs a question?" | Pass `max_output_tokens` via `max_tokens_param`; concise instruction. | `pipeline` (SOLUTION 1.4) |
| 1.5 | "How many questions repeat?" | Normalised question as key, `|customer_id` if order-specific. | `cache` (SOLUTION 1.5) |
| 1.6 | "Repeats with different wording?" | Embed, cosine vs cached non-order entries, threshold 0.92; count embedding cost. | `cache` (SOLUTION 1.6) |
| 1.7 | "What changes between two calls — and where is it in the prompt?" | Static system message, dynamic user message (`Customer ID`, `Today`, `Context`, `Question`). | `prompts` (SOLUTION 1.7) |
| 2.1 | "Which models are in models.json?" | `deploy_open_model` / `deploy_selfhosted_model` → `terraform apply` → `doctor`. | infra README / tfvars example |
| 2.2 | "Which model is good enough for which question type?" | `compare --models premium,balanced,economy,open`, set `default_model` + router mapping. | solution `strategy.json` |
| 2.3 | "Is the open model answering in another language or inventing rules?" | Append the extra instruction for `open`/`selfhosted`/`custom`/`fw`/`fw_*`. | `prompts` (SOLUTION 2.3) |
| 2.4 | "Which tier could an open premium model replace?" | `compare --models balanced,premium,fw`; compare cost per success **and** p95; put the winner into `TIER_MODELS` / `Router.TierModels` or `default_model`. | solution `strategy.json` + router mapping |
| 3.1 | "Which questions are obviously easy?" | Implement the 4 rules in order (order-specific first). | `router` (SOLUTION 3.1) |
| 3.2 | "Let the cheapest model decide." | economy, temperature 0 (omitted when `supports_temperature` is false), max 5 tokens, map SIMPLE/STANDARD/COMPLEX. | `router` (SOLUTION 3.2) |
| 3.3 | "What if the cheap model isn't sure?" | Add the ESCALATE instruction; on `ESCALATE` re-run with next tier; cost every call. | `pipeline` (SOLUTION 3.3) |
| 3.4 | "What does a 429 response tell you?" | Retry 429/5xx up to 4×, `retry-after-ms` → `retry-after` → 1/2/4/8 s + jitter. | `llm_client` / `LlmClient.cs` (SOLUTION 3.4) |
| 3.5 | "Which policy limits tokens per consumer?" | `llm-token-limit` counter-key subscription, `llm-emit-token-metric`, retry around `forward-request`, pool backend. | `infra/policies/ai-gateway-solution.xml` |
| 3.6 | "What should happen when Azure says 429?" | `apim_policy_file = "policies/ai-gateway-multiprovider.xml"` + `deploy_fireworks = true`, apply; watch `x-tokenwars-provider`. | `infra/policies/ai-gateway-multiprovider.xml` |

---

## 8. Running the leaderboard

```bash
cd leaderboard
az containerapp up --name tokenwars-leaderboard --resource-group <rg> --source . \
  --ingress external --target-port 8080 --env-vars LEADERBOARD_SUBMIT_KEY=<shared-key>
az containerapp update --name tokenwars-leaderboard --resource-group <rg> --min-replicas 1 --max-replicas 1
```
- Put the printed URL on the big screen (`/`, refreshes every 10 s). Teams set `leaderboard_url` / `leaderboard_key`
  in `terraform.tfvars` (→ `.env`) or directly in `.env`.
- Validity: full run (100 items) and pass rate ≥ 85 %; mock runs are rejected. Partial or failing runs appear greyed
  out below the ranking with the reason.
- Data is in the container (`/app/data/submissions.json`); keep 1 replica. Export with `GET /api/submissions`.
- Local fallback (laptop on the venue Wi-Fi): `python3 leaderboard/server.py` (port 8080).
- Reset: delete `data/submissions.json` and restart (or redeploy).

---

## 9. Debrief (10–15 min)

1. What moved the needle most? (Usually: context size → model choice → caching → routing.)
2. Where did quality break? (economy on policy reasoning, semantic cache threshold, retrieval misses on 2-doc questions.)
3. What would you do in production? Evals in CI, token budgets per team at the gateway, prompt-cache-friendly
   layout by default, model routing with escalation, observability of tokens per feature/customer.
4. Hidden costs: self-hosting infra, engineering time, latency SLOs, data residency (Global vs DataZone deployments;
   Fireworks Data Zone Standard is US-only and Fireworks is currently outside the EU Data Boundary).
5. Multi-provider (3.6): failover keeps the service up, but a **different model answers silently** — quality drift,
   different prompt behaviour, one shared token budget. Would you rather fail fast or fail over?
6. Own vs rent: "Own the Weights" break-even (section 5) — hosting fee vs per-token, and PTU only for steady high load.
7. Cleanup: `terraform destroy`.

---

## 10. Cost estimate for the event (rough, list prices)

| Item | Per team | 10 teams |
|---|---|---|
| Baseline runs (3 × ~$7–8 on gpt-5.6-sol) | ~$21–24 | ~$210–240 |
| Optimised runs, `ask`, experiments (~20 runs, routing-dependent) | budget ~$6–20 | budget ~$60–200 |
| `compare` runs (30 items × 4 models, compact prompt; ~$2–4 more with the baseline prompt) | ~$0.5–4 | ~$5–40 |
| Judge (GPT-5.5; roughly 2.5× Terra token rates; measure during dry run) | budget ~$12–25 | budget ~$120–250 |
| APIM StandardV2_1 (~$0.96/h × ~6 h) | ~$6 | ~$60 |
| Ollama on ACA, 4 vCPU/8 GiB (~$0.45/h × ~3 h, optional) | ~$1.5 | ~$15 |
| Fireworks Arena, optional: `compare` 30 items × `fw` plus 2–4 full runs ¹ | ~$0.2–1 | ~$2–10 |
| Multi-provider failover tests (3.6, optional, a few dozen calls) | < $0.5 | < $5 |
| Fireworks Foundry resource + pay-per-token deployments (no hourly fee) | $0 idle | $0 idle |
| Log Analytics / App Insights | < $1 | < $10 |
| Leaderboard (Container App, shared) | — | < $2 |
| **Total (planning allowance; recheck after dry run)** | **~$50–90** | **~$500–900** |

¹ From `pricing.json` Fireworks values, which are **`verified: false`** (third-party tracker, Oct 2026) — re-check at T-7.

Coach subscription (not per team): dry run ≈ one team ($20–45), "Own the Weights" ≈ $20 (mostly hosting fee while
deployed — delete it right after the session).

Biggest risk to the budget: teams looping full baseline runs. Suggest `--limit 20` for experiments and full runs only
before submitting. Remind everyone to `terraform destroy` — APIM and Container Apps bill per hour even when idle.

---

## 11. Dry-run validation (coach, T-7 days)

Run the whole kit once in a **fresh subscription** (no registered resource providers, no Fireworks feature) — that is
what teams will have. The items below could not be verified without a live subscription (sources: the infra
hand-off VALIDATE list — also marked `VALIDATE` in `infra/` and `infra/scripts/` — and
[own-the-weights/README.md](own-the-weights/README.md) sections 8 and 12).
Fix the defaults or add a note to [troubleshooting.md](troubleshooting.md) for anything that fails.

**Terraform / Microsoft Foundry**
- [ ] **azurerm 5.x:** `terraform init -upgrade && terraform validate && terraform apply` succeeds in a fresh subscription
      (resource providers registered by `resource_providers_to_register`; `azurerm_role_assignment` /
      `azurerm_resource_group` unaffected by the parts of the 5.0 upgrade guide that could not be read). `terraform fmt -check`
      too — nothing was run with Terraform when the change was made.
- [ ] **Project endpoint key** `"AI Foundry API"` in the project's `endpoints` map: `terraform output foundry_project_endpoint`
      shows a real endpoint (the output falls back automatically).
- [ ] **Double auth header** of the apps (`api-key` + `Authorization`) works against `*.services.ai.azure.com`:
      `doctor` is green with `ai_endpoint_style = "foundry"`. If you see 401s, make `"openai"` the event default.
- [ ] `terraform output foundry_portal_hint` → the project opens in https://ai.azure.com.

**Fireworks on Foundry (Challenge 2.4)**
- [ ] **Feature namespace** `Microsoft.CognitiveServices` for `Fireworks.EnableDeploy`: `check-fireworks-prereqs.sh --register`
      registers it (the script falls back to `az feature list`); note the real time to `Registered`.
- [ ] **Model availability** of `FW-GLM-5.3-Flash` as Global Standard in `eastus2`
      (script step 6). Per-token offers can retire with 15 days' notice — re-check now, swap to an alternative if needed.
- [ ] **Deployment `format`**: compare the script's *format* column with `fireworks_model_format` (default `"Fireworks"`;
      `"FireworksCustom"` is for imported custom weights only).
- [ ] **Model version**: `null` (= default, catalog shows "Version: 1") deploys.
- [ ] **Deployer role**: does Owner/Contributor alone deploy, or is `fireworks_grant_deployer_role = true` needed? Does the
      tenant show "Foundry Owner" or still "Azure AI Owner" (`fireworks_deployer_role`)?
- [ ] **Gateway routing** for `fw` / `fw_*`: `via_gateway: true`, `doctor` green using the APIM key, then
      `compare --models balanced,premium,fw`; record pass rate, cost per success and p95 for section 6.
- [ ] **Prices**: confirm the `fw-*` prices in the Azure pricing calculator; update `pricing.json` and set `"verified": true`
      only for confirmed values.
- [ ] Stretch check: `cached_tokens` with and without `prompt_cache_key`.

**Multi-provider failover (Challenge 3.6)**
- [ ] Policy expressions compile on save (`terraform apply` with `ai-gateway-multiprovider.xml` fails fast if not).
- [ ] Backend 429 → Fireworks retry works; `x-tokenwars-provider: fireworks` appears (curl in section 3).
- [ ] Fireworks models accept the rewritten body (no other GPT-5.x-only parameter rejected).
- [ ] All policies emit lowercase `team`/`provider` dimensions. Dashboard **Provider Tokens** is attributed to the
      final Fireworks provider/model after failover and includes embeddings. Native metrics retain the requested
      route; do not add them to the outbound counter. Failed attempts without usage remain uncounted.
- [ ] With an **open circuit breaker** the gateway may raise an error instead of a 503, so `on-error` runs and no
      Fireworks retry happens → keep `deploy_secondary_region = false` for the demo, use a low deployment TPM, expect the
      first few 429s to fail over.
- [ ] `fireworks_backend_auth = "managed_identity"`: do Fireworks deployments accept Entra ID tokens? Keep the default
      `api_key` unless confirmed.

**"Own the Weights" (coach subscription)**
- [ ] Base model id for the REST job (`BASE_MODEL=Ministral-3B`; alternatives `gpt-oss-20b`, `Qwen3-32B`) — `finetune preflight`.
- [ ] Data-plane endpoint `https://<account>.services.ai.azure.com/openai/v1` for `/files` and `/fine_tuning/jobs`
      (fallback `*.openai.azure.com`, `FOUNDRY_ENDPOINT`).
- [ ] Deployment model `format` and `version: "1"` for the open-weight fine-tune in the ARM PUT (portal deploy as fallback).
- [ ] `DEPLOY_CAPACITY` (default 50) unit and the available Global Standard fine-tuned quota.
- [ ] File processing status values and the `capabilities.fine_tune` field used by `preflight`; UTF-8 BOM accepted
      (`--no-bom` if not).
- [ ] Fine-tuning prices and hosting fees (gpt-oss-20b not on the pricing page); typical job duration (assumed 1–3 h);
      PTU reference numbers in `breakeven.py` are illustrative.
- [ ] `generate_dataset.py --mock` runs offline; the real run reports leakage; 10 examples spot-checked; train ≥ 300,
      validation ≥ 30.
- [ ] Job `succeeded`, validation loss sane; deployment SKU **GlobalStandard** (no PTU); `finetune test` answers;
      `register_custom_model.py --write-pricing` done and `doctor` shows `custom`.
- [ ] Calendar reminder to **delete the deployment** after the session; `pricing.json` reverted, `custom` removed.

**Core kit (unchanged, still run once)**
- [ ] `doctor`, baseline `run --limit 20`, solution full run (valid, ≥ 85 %), `compare`, APIM solution policy + 429 demo,
      leaderboard submit.
